"""Workflow B: the multi-stage candidate ranking pipeline.

    Stage 1  pgvector + full-text (hybrid) retrieval .......... whole pool -> top 100
    Stage 2  SQL metadata filters (experience, skills, ...) .... top 100   -> top 20
    Stage 3  Bedrock Nova re-ranking with explanations ......... top 20 only

Every query runs on the caller's `user_tx` connection, so Row-Level Security limits the pool to
candidates the recruiter is allowed to see (their own applicants, or applicants who opted into
talent search). The LLM never sees the whole database, and its score is blended with
deterministic scores computed from the database.
"""
import re
from dataclasses import dataclass, field

from ..db import vector_literal
from . import embeddings, llm

STAGE1_LIMIT = 100
STAGE2_LIMIT = 20


@dataclass
class JobSkill:
    skill_id: int
    name: str
    is_required: bool
    weight: float


@dataclass
class SearchSpec:
    title: str
    description: str
    skills: list[JobSkill] = field(default_factory=list)       # used for scoring
    must_have_skill_ids: list[int] = field(default_factory=list)  # hard filter (talent search)
    min_experience: float | None = None
    location: str | None = None
    max_notice_days: int | None = None
    job_id: int | None = None


def _like_pattern(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text.strip()) + "%"


def _keyword_query(spec: SearchSpec) -> str:
    """websearch_to_tsquery input: an OR of the job's skills and title words (user text is quoted)."""
    terms = [s.name for s in spec.skills] + [w for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.]{2,}", spec.title)]
    terms = [t.replace('"', "") for t in terms][:25]
    return " or ".join(f'"{t}"' for t in terms) or '""'


def _recommendation(score: float) -> str:
    return "STRONG_MATCH" if score >= 80 else "MATCH" if score >= 65 else "WEAK_MATCH" if score >= 45 else "REJECT"


def _skill_score(spec: SearchSpec, have: set[int]) -> float | None:
    if spec.skills:
        total = sum(s.weight * (2 if s.is_required else 1) for s in spec.skills)
        got = sum(s.weight * (2 if s.is_required else 1) for s in spec.skills if s.skill_id in have)
        return round(100 * got / total, 2) if total else None
    if spec.must_have_skill_ids:
        return round(100 * len(set(spec.must_have_skill_ids) & have) / len(spec.must_have_skill_ids), 2)
    return None


def _experience_score(spec: SearchSpec, years: float) -> float:
    if spec.min_experience and spec.min_experience > 0:
        return round(min(100.0, 100 * years / spec.min_experience), 2)
    return round(min(100.0, 70 + 10 * years), 2)


async def rank(conn, spec: SearchSpec, *, pool: str, use_llm: bool = True) -> tuple[list[dict], bool]:
    """Returns (ranked candidates, whether the LLM contributed). pool is 'applications' or 'talent'."""
    query_vector = vector_literal(await embeddings.embed_query(
        embeddings.job_representation(spec.title, spec.description, [s.name for s in spec.skills])))
    keyword = _keyword_query(spec)

    # ---- Stage 1: hybrid retrieval -------------------------------------------------------------
    if pool == "applications":
        stage1 = await conn.fetch(
            """WITH q AS (SELECT $1::vector AS v, websearch_to_tsquery('english', $2) AS tsq)
               SELECT a.application_id, a.applicant_id, a.resume_id,
                      1 - (re.embedding <=> q.v)              AS semantic,
                      ts_rank_cd(r.search_vector, q.tsq, 32)   AS keyword
               FROM applications a
               JOIN resumes r            ON r.resume_id = a.resume_id
               JOIN resume_embeddings re ON re.resume_id = a.resume_id
               CROSS JOIN q
               WHERE a.job_id = $3 AND a.status NOT IN ('WITHDRAWN', 'REJECTED', 'HIRED')
               ORDER BY re.embedding <=> q.v
               LIMIT $4""",
            query_vector, keyword, spec.job_id, STAGE1_LIMIT)
    else:
        stage1 = await conn.fetch(
            """WITH q AS (SELECT $1::vector AS v, websearch_to_tsquery('english', $2) AS tsq)
               SELECT NULL::bigint AS application_id, r.applicant_id, r.resume_id,
                      1 - (re.embedding <=> q.v)              AS semantic,
                      ts_rank_cd(r.search_vector, q.tsq, 32)   AS keyword
               FROM resumes r
               JOIN resume_embeddings re ON re.resume_id = r.resume_id
               JOIN applicants ap        ON ap.applicant_id = r.applicant_id
               CROSS JOIN q
               WHERE r.is_active AND r.processing_status = 'COMPLETED' AND ap.is_discoverable
               ORDER BY re.embedding <=> q.v
               LIMIT $3""",
            query_vector, keyword, STAGE1_LIMIT)
    if not stage1:
        return [], False
    retrieval = {r["applicant_id"]: r for r in stage1}

    # ---- Stage 2: SQL metadata filters -------------------------------------------------------------
    hard_filters = pool == "talent"
    stage2 = await conn.fetch(
        """SELECT c.* FROM (
               SELECT ap.applicant_id, ap.first_name, ap.last_name, ap.headline, ap.location, ap.notice_period_days,
                      calculate_candidate_experience(ap.applicant_id) AS experience_years,
                      ARRAY(SELECT cs.skill_id FROM candidate_skills cs WHERE cs.applicant_id = ap.applicant_id) AS skill_ids
               FROM applicants ap
               WHERE ap.applicant_id = ANY($1::bigint[])
           ) c
           WHERE NOT $2::boolean OR (
                 ($3::numeric IS NULL OR c.experience_years >= $3)
             AND ($4::text    IS NULL OR c.location ILIKE $4)
             AND ($5::int     IS NULL OR c.notice_period_days IS NULL OR c.notice_period_days <= $5)
             AND (cardinality($6::bigint[]) = 0 OR $6::bigint[] <@ c.skill_ids))""",
        list(retrieval), hard_filters, spec.min_experience,
        _like_pattern(spec.location) if spec.location else None, spec.max_notice_days, spec.must_have_skill_ids)

    candidates = []
    for c in stage2:
        r = retrieval[c["applicant_id"]]
        have = set(c["skill_ids"])
        semantic = max(0.0, min(100.0, (float(r["semantic"]) - 0.2) / 0.6 * 100))
        years = float(c["experience_years"] or 0)
        candidates.append({
            "application_id": r["application_id"], "applicant_id": c["applicant_id"], "resume_id": r["resume_id"],
            "name": f"{c['first_name']} {c['last_name']}", "headline": c["headline"], "location": c["location"],
            "experience_years": years, "skill_ids": have,
            "semantic_score": round(semantic, 2),
            "hybrid": 0.75 * float(r["semantic"]) + 0.25 * float(r["keyword"]),
            "skill_score": _skill_score(spec, have),
            "experience_score": _experience_score(spec, years),
            "missing_skills": [s.name for s in sorted(spec.skills, key=lambda s: not s.is_required) if s.skill_id not in have],
            "matched_skills": [s.name for s in spec.skills if s.skill_id in have],
        })
    candidates.sort(key=lambda x: x["hybrid"], reverse=True)

    # ---- Stage 3: LLM re-ranking of the top 20 only --------------------------------------------------
    llm_results = {}
    shortlist = candidates[:STAGE2_LIMIT]
    if use_llm and shortlist and llm.available():
        llm_results = await llm.rerank(_job_payload(spec), await _candidate_payloads(conn, shortlist))

    for i, c in enumerate(candidates):
        result = llm_results.get(f"c{i + 1}") if i < STAGE2_LIMIT else None
        skill = c["skill_score"]
        if result:
            parts = [(0.45, result.overall_score), (0.15, c["semantic_score"]), (0.15, c["experience_score"])]
            parts.append((0.25, skill if skill is not None else result.skill_score))
            c.update(education_score=result.education_score, strengths=result.strengths,
                     ai_explanation=result.explanation, llm_used=True)
        else:
            parts = ([(0.5, skill), (0.3, c["semantic_score"]), (0.2, c["experience_score"])] if skill is not None
                     else [(0.6, c["semantic_score"]), (0.4, c["experience_score"])])
            strengths = []
            if c["matched_skills"]:
                strengths.append("Has " + ", ".join(c["matched_skills"][:4]))
            if c["experience_years"]:
                strengths.append(f"{c['experience_years']:g} years of experience")
            c.update(education_score=None, strengths=strengths, ai_explanation=None, llm_used=False)
        c["overall_score"] = round(sum(w * v for w, v in parts), 2)
        c["recommendation"] = _recommendation(c["overall_score"])
        if c["skill_score"] is None:
            c["skill_score"] = result.skill_score if result else None

    candidates.sort(key=lambda x: x["overall_score"], reverse=True)
    for c in candidates:
        c.pop("skill_ids", None)
        c.pop("hybrid", None)
    return candidates, bool(llm_results)


def _job_payload(spec: SearchSpec) -> dict:
    return {
        "title": spec.title,
        "required_skills": [s.name for s in spec.skills if s.is_required],
        "preferred_skills": [s.name for s in spec.skills if not s.is_required],
        "min_experience_years": spec.min_experience,
        "description": spec.description[:3000],
    }


async def _candidate_payloads(conn, shortlist: list[dict]) -> list[dict]:
    """Structured, de-identified profiles. No names, contact details or locations go to the LLM."""
    rows = await conn.fetch(
        """SELECT r.resume_id, r.parsed_data,
                  ARRAY(SELECT s.skill_name FROM candidate_skills cs JOIN skills s ON s.skill_id = cs.skill_id
                        WHERE cs.applicant_id = r.applicant_id ORDER BY s.skill_name) AS skills
           FROM resumes r WHERE r.resume_id = ANY($1::bigint[])""",
        [c["resume_id"] for c in shortlist])
    by_resume = {r["resume_id"]: r for r in rows}
    payloads = []
    for i, c in enumerate(shortlist):
        row = by_resume.get(c["resume_id"])
        parsed = (row["parsed_data"] if row else None) or {}
        payloads.append({
            "ref": f"c{i + 1}",
            "experience_years": c["experience_years"],
            "skills": (list(row["skills"]) if row else [])[:40],
            "roles": [f"{e.get('title')} at {e.get('company')}" for e in parsed.get("experience", [])][:6],
            "education": [f"{e.get('degree')} {e.get('field_of_study') or ''}".strip() for e in parsed.get("education", [])][:3],
            "projects": [f"{p.get('title')} ({', '.join(p.get('technologies', [])[:6])})" for p in parsed.get("projects", [])][:5],
            "summary": (parsed.get("summary") or "")[:400],
        })
    return payloads
