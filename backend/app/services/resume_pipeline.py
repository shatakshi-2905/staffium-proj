"""Workflow A: process a resume exactly once after upload.

    S3 object -> text -> structured metadata (Bedrock Nova, rule-based fallback)
              -> skills normalised against the dictionary -> embedding -> one DB transaction

Recruiter searches only ever read the stored results; nothing is re-parsed at search time.
"""
import asyncio
import logging
from datetime import date

from ..db import system_tx, vector_literal
from ..errors import AppError
from . import embeddings, llm, storage
from .parsing import (embedding_text, extract_text, normalize_skill, parse_month, rule_based_extract, scan_text_for_skills,
                      without_skill_lists)

log = logging.getLogger("staffium.resumes")
MAX_ATTEMPTS = 3
_slots = asyncio.Semaphore(2)   # PDF parsing + embedding are CPU-bound; cap concurrent work per instance


async def skill_dictionary(conn) -> tuple[dict[str, int], dict[int, str]]:
    """({lower(name): id}, {id: display name})"""
    rows = await conn.fetch("SELECT skill_id, skill_name FROM skills")
    return {r["skill_name"].lower(): r["skill_id"] for r in rows}, {r["skill_id"]: r["skill_name"] for r in rows}


async def process_resume(resume_id: int, data: bytes | None = None) -> None:
    async with _slots:
        await _process(resume_id, data)


async def _process(resume_id: int, data: bytes | None) -> None:
    async with system_tx() as conn:
        row = await conn.fetchrow(
            """UPDATE resumes SET processing_status = 'PROCESSING', processing_attempts = processing_attempts + 1
               WHERE resume_id = $1 AND processing_status IN ('UPLOADED', 'PROCESSING')
               RETURNING applicant_id, s3_object_key, file_type, processing_attempts""",
            resume_id,
        )
    if row is None:
        return

    try:
        if data is None:
            data = await storage.get_resume(row["s3_object_key"])
        text = await asyncio.wait_for(asyncio.to_thread(extract_text, data, row["file_type"]), timeout=60)
        parsed = await llm.extract_resume(text) or rule_based_extract(text)

        async with system_tx() as conn:
            dictionary, id_to_name = await skill_dictionary(conn)
            person = await conn.fetchrow("SELECT first_name, last_name, location FROM applicants WHERE applicant_id = $1",
                                         row["applicant_id"])
        extracted_ids = {sid for sid in (normalize_skill(s, dictionary) for s in parsed.get("skills", [])) if sid}
        scanned_ids = scan_text_for_skills(text, dictionary)
        all_ids = extracted_ids | scanned_ids
        parsed["matched_skills"] = sorted(id_to_name[i] for i in all_ids)
        # Skills that appear where work is described, not only in a "Skills:" list. Ranking gives
        # listed-only skills half credit, so a keyword-stuffed list can't pass for experience.
        parsed["evidenced_skills"] = sorted(id_to_name[i] for i in scan_text_for_skills(without_skill_lists(text), dictionary))

        # Nothing that identifies the person (or their college, city or career gaps) goes into the embedding.
        redact = [person["first_name"], person["last_name"], person["location"],
                  parsed.get("name"), parsed.get("location")] if person else [parsed.get("name"), parsed.get("location")]
        for part in list(redact):
            if part and " " in part.strip():
                redact.extend(part.split())
        vector = await embeddings.embed_passage(embeddings.resume_representation(parsed, embedding_text(text, redact)))
        await _save(resume_id, row["applicant_id"], text, parsed, vector, extracted_ids, scanned_ids)
        log.info("resume %s processed (%d skills, extractor=%s)", resume_id, len(all_ids), parsed.get("extracted_by"))

    except AppError as exc:   # the file itself is the problem: tell the applicant, don't retry
        await _fail(resume_id, exc.message, final=True)
    except Exception:
        log.exception("resume %s processing error", resume_id)
        final = row["processing_attempts"] >= MAX_ATTEMPTS
        await _fail(resume_id, "We couldn't process this file. Please try uploading it again.", final=final)


async def _fail(resume_id: int, message: str, final: bool) -> None:
    async with system_tx() as conn:
        await conn.execute(
            "UPDATE resumes SET processing_status = $2, processing_error = $3 WHERE resume_id = $1",
            resume_id, "FAILED" if final else "UPLOADED", message if final else None,
        )


async def _save(resume_id, applicant_id, text, parsed, vector, extracted_ids, scanned_ids) -> None:
    async with system_tx() as conn:
        await conn.execute(
            """UPDATE resumes SET extracted_text = $2, parsed_data = $3, processing_status = 'COMPLETED',
                      processing_error = NULL, processed_at = now()
               WHERE resume_id = $1""",
            resume_id, text, parsed,
        )
        await conn.execute(
            """INSERT INTO resume_embeddings (resume_id, embedding, model_name, model_version)
               VALUES ($1, $2::vector, $3, $4)
               ON CONFLICT (resume_id) DO UPDATE SET embedding = EXCLUDED.embedding, model_name = EXCLUDED.model_name,
                   model_version = EXCLUDED.model_version, created_at = now()""",
            resume_id, vector_literal(vector), embeddings.model_name(), embeddings.MODEL_VERSION,
        )

        still_active = await conn.fetchval("SELECT is_active FROM resumes WHERE resume_id = $1", resume_id)
        if not still_active:
            return   # a newer upload replaced this one while it was processing; don't touch the profile

        # Skills: resume-derived rows are replaced; skills the applicant added by hand are never touched.
        await conn.execute("DELETE FROM candidate_skills WHERE applicant_id = $1 AND source IN ('RESUME', 'AI_EXTRACTED')", applicant_id)
        source_for = {sid: "AI_EXTRACTED" for sid in extracted_ids if parsed.get("extracted_by") == "llm"}
        rows = [(applicant_id, sid, source_for.get(sid, "RESUME")) for sid in (extracted_ids | scanned_ids)]
        if rows:
            await conn.executemany(
                "INSERT INTO candidate_skills (applicant_id, skill_id, source) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", rows)

        # Education / experience / projects imported from the resume replace the previous import.
        for table in ("education", "experience", "projects"):
            await conn.execute(f"DELETE FROM {table} WHERE applicant_id = $1 AND source = 'RESUME'", applicant_id)

        for e in parsed.get("education", []):
            if e.get("institution") and e.get("degree"):
                start, end = e.get("start_year"), e.get("end_year")
                if start and end and end < start:
                    start = end = None
                await conn.execute(
                    """INSERT INTO education (applicant_id, institution, degree, field_of_study, start_year, end_year, cgpa, source)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'RESUME')""",
                    applicant_id, e["institution"][:200], e["degree"][:120], (e.get("field_of_study") or None), start, end, e.get("cgpa"))

        today = date.today()
        for e in parsed.get("experience", []):
            start = parse_month(e.get("start"))
            end = None if e.get("is_current") else parse_month(e.get("end"))
            if not start or start > today or (end and end < start):
                continue
            await conn.execute(
                """INSERT INTO experience (applicant_id, company_name, designation, start_date, end_date, description, is_current, source)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, 'RESUME')""",
                applicant_id, e["company"][:200], e["title"][:150], start, end, e.get("summary"), bool(e.get("is_current")) and end is None)

        for p in parsed.get("projects", []):
            await conn.execute(
                "INSERT INTO projects (applicant_id, title, description, technologies, source) VALUES ($1, $2, $3, $4, 'RESUME')",
                applicant_id, p["title"][:200], p.get("summary"), [t[:60] for t in p.get("technologies", [])][:15])

        # Fill profile gaps (never overwrite what the applicant typed).
        await conn.execute(
            """UPDATE applicants SET headline = coalesce(headline, $2), location = coalesce(location, $3)
               WHERE applicant_id = $1""",
            applicant_id, (parsed.get("headline") or None) and parsed["headline"][:160],
            (parsed.get("location") or None) and parsed["location"][:150])
