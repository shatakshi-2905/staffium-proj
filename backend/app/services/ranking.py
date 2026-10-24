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


def _skill_score(spec: SearchSpec, have: set[int], shown: set[int] | None = None) -> float | None:
    """Weighted share of the job's skills the candidate has. A skill only *listed* (never shown in work
    history or added by the candidate themselves) earns half credit: this is what stops a
    keyword-stuffed skills list from outranking real experience (see eval/ for the measurement)."""
    shown = have if shown is None else shown

    def credit(sid: int) -> float:
        return 1.0 if sid in shown else 0.5 if sid in have else 0.0

    if spec.skills:
        total = sum(s.weight * (2 if s.is_required else 1) for s in spec.skills)
        got = sum(s.weight * (2 if s.is_required else 1) * credit(s.skill_id) for s in spec.skills)
        return round(100 * got / total, 2) if total else None
    if spec.must_have_skill_ids:
        return round(100 * sum(credit(sid) for sid in spec.must_have_skill_ids) / len(spec.must_have_skill_ids), 2)
    return None


async def _skill_evidence(conn, resume_ids: list[int], applicant_ids: list[int], spec: SearchSpec):
    """Returns f(applicant_id, resume_id, have) -> skill ids with evidence: used in the resume's work
    history/projects, or added by the candidate by hand. Resumes processed before evidence was
    recorded count every skill as shown."""
    rows = await conn.fetch("SELECT resume_id, parsed_data -> 'evidenced_skills' AS ev FROM resumes WHERE resume_id = ANY($1::bigint[])",
                            resume_ids)
    ev = {r["resume_id"]: r["ev"] for r in rows}
    manual = {}
    for r in await conn.fetch("""SELECT applicant_id, array_agg(skill_id) AS ids FROM candidate_skills
                                 WHERE applicant_id = ANY($1::bigint[]) AND source = 'MANUAL' GROUP BY applicant_id""", applicant_ids):
        manual[r["applicant_id"]] = set(r["ids"])
    wanted = {s.skill_id for s in spec.skills} | set(spec.must_have_skill_ids)
    names_needed = await conn.fetch("SELECT skill_id, lower(skill_name) AS n FROM skills WHERE skill_id = ANY($1::bigint[])", list(wanted))
    name_of = {r["skill_id"]: r["n"] for r in names_needed}

    def shown(applicant_id: int, resume_id: int, have: set[int]) -> set[int]:
        evidenced = ev.get(resume_id)
        if not isinstance(evidenced, list):
            return set(have)
        lowered = {str(x).lower() for x in evidenced}
        return {sid for sid in have if name_of.get(sid) in lowered} | (manual.get(applicant_id, set()) & have)

    return shown


def _experience_score(spec: SearchSpec, years: float) -> float:
    if spec.min_experience and spec.min_experience > 0:
        return round(min(100.0, 100 * years / spec.min_experience), 2)
    return round(min(100.0, 70 + 10 * years), 2)


async def rank(conn, spec: SearchSpec, *, pool: str, use_llm: bool = True,
               only_application_ids: list[int] | None = None, trace: dict | None = None) -> tuple[list[dict], bool]:
    """Returns (ranked candidates, whether the LLM contributed). pool is 'applications' or 'talent'.

    only_application_ids re-scores specific applications (used by human review), whatever their
    status. trace, if given, is filled with what each stage kept, for the evaluation harness."""
    query_vector = vector_literal(await embeddings.embed_query(
        embeddings.job_representation(spec.title, spec.description, [s.name for s in spec.skills])))
    keyword = _keyword_query(spec)

    # ---- Stage 1: hybrid retrieval -------------------------------------------------------------
    # Union of the nearest resumes by meaning (pgvector, HNSW index) and the best keyword matches
    # (tsvector, GIN index). The keyword half rescues resumes whose wording embeds poorly but name
    # the right tools; the vector half finds people who describe the work without the keywords.
    if pool == "applications":
        stage1 = await conn.fetch(
            """WITH q AS (SELECT $1::vector AS v, websearch_to_tsquery('english', $2) AS tsq),
               eligible AS (
                   SELECT a.application_id FROM applications a
                   WHERE a.job_id = $3
                     AND CASE WHEN $5::bigint[] IS NULL THEN a.status NOT IN ('WITHDRAWN', 'REJECTED', 'HIRED')
                              ELSE a.application_id = ANY($5::bigint[]) END),
               by_vector AS (
                   SELECT a.application_id FROM applications a
                   JOIN resume_embeddings re ON re.resume_id = a.resume_id
                   WHERE a.application_id IN (SELECT application_id FROM eligible)
                   ORDER BY re.embedding <=> $1::vector LIMIT $4),
               by_keyword AS (
                   SELECT a.application_id FROM applications a
                   JOIN resumes r ON r.resume_id = a.resume_id CROSS JOIN q
                   WHERE a.application_id IN (SELECT application_id FROM eligible) AND r.search_vector @@ q.tsq
                   ORDER BY ts_rank_cd(r.search_vector, q.tsq, 32) DESC LIMIT $4)
               SELECT a.application_id, a.applicant_id, a.resume_id,
                      1 - (re.embedding <=> q.v)              AS semantic,
                      ts_rank_cd(r.search_vector, q.tsq, 32)   AS keyword,
                      a.application_id IN (SELECT application_id FROM by_vector) AS via_vector
               FROM applications a
               JOIN resumes r            ON r.resume_id = a.resume_id
               JOIN resume_embeddings re ON re.resume_id = a.resume_id
               CROSS JOIN q
               WHERE a.application_id IN (SELECT application_id FROM by_vector UNION SELECT application_id FROM by_keyword)
               ORDER BY semantic DESC""",
            query_vector, keyword, spec.job_id, STAGE1_LIMIT, only_application_ids)
    else:
        stage1 = await conn.fetch(
            """WITH q AS (SELECT $1::vector AS v, websearch_to_tsquery('english', $2) AS tsq),
               by_vector AS (
                   SELECT r.resume_id FROM resumes r
                   JOIN resume_embeddings re ON re.resume_id = r.resume_id
                   JOIN applicants ap        ON ap.applicant_id = r.applicant_id
                   WHERE r.is_active AND r.processing_status = 'COMPLETED' AND ap.is_discoverable
                   ORDER BY re.embedding <=> $1::vector LIMIT $3),
               by_keyword AS (
                   SELECT r.resume_id FROM resumes r
                   JOIN applicants ap ON ap.applicant_id = r.applicant_id CROSS JOIN q
                   WHERE r.is_active AND r.processing_status = 'COMPLETED' AND ap.is_discoverable AND r.search_vector @@ q.tsq
                   ORDER BY ts_rank_cd(r.search_vector, q.tsq, 32) DESC LIMIT $3)
               SELECT NULL::bigint AS application_id, r.applicant_id, r.resume_id,
                      1 - (re.embedding <=> q.v)              AS semantic,
                      ts_rank_cd(r.search_vector, q.tsq, 32)   AS keyword,
                      r.resume_id IN (SELECT resume_id FROM by_vector) AS via_vector
               FROM resumes r
               JOIN resume_embeddings re ON re.resume_id = r.resume_id
               CROSS JOIN q
               WHERE r.resume_id IN (SELECT resume_id FROM by_vector UNION SELECT resume_id FROM by_keyword)
               ORDER BY semantic DESC""",
            query_vector, keyword, STAGE1_LIMIT)
    if trace is not None:
        trace["stage1"] = [{"applicant_id": r["applicant_id"], "semantic": float(r["semantic"]), "keyword": float(r["keyword"]),
                            "via": "vector" if r["via_vector"] else "keyword"} for r in stage1]
    if not stage1:
        if trace is not None:
            trace["stage2"], trace["llm_reviewed"] = [], []
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

    evidence = await _skill_evidence(conn, [r["resume_id"] for r in stage1], [c["applicant_id"] for c in stage2], spec)

    candidates = []
    for c in stage2:
        r = retrieval[c["applicant_id"]]
        have = set(c["skill_ids"])
        shown = evidence(c["applicant_id"], r["resume_id"], have)
        semantic = max(0.0, min(100.0, (float(r["semantic"]) - 0.2) / 0.6 * 100))
        years = float(c["experience_years"] or 0)
        candidates.append({
            "application_id": r["application_id"], "applicant_id": c["applicant_id"], "resume_id": r["resume_id"],
            "name": f"{c['first_name']} {c['last_name']}", "headline": c["headline"], "location": c["location"],
            "experience_years": years, "skill_ids": have,
            "semantic_score": round(semantic, 2),
            "hybrid": 0.75 * float(r["semantic"]) + 0.25 * float(r["keyword"]),
            "skill_score": _skill_score(spec, have, shown),
            "experience_score": _experience_score(spec, years),
            "missing_skills": [s.name for s in sorted(spec.skills, key=lambda s: not s.is_required) if s.skill_id not in have],
            "matched_skills": [s.name for s in spec.skills if s.skill_id in have],
            "listed_only_skills": [s.name for s in spec.skills if s.skill_id in have and s.skill_id not in shown],
        })
    candidates.sort(key=lambda x: x["hybrid"], reverse=True)
    if trace is not None:
        trace["stage2"] = [c["applicant_id"] for c in candidates]
        trace["llm_reviewed"] = [c["applicant_id"] for c in candidates[:STAGE2_LIMIT]] if use_llm and llm.available() else []

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
            shown_names = [n for n in c["matched_skills"] if n not in c["listed_only_skills"]]
            if shown_names:
                strengths.append("Has used " + ", ".join(shown_names[:4]))
            if c["listed_only_skills"]:
                strengths.append("Lists " + ", ".join(c["listed_only_skills"][:3]) + " without describing where they were used")
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
