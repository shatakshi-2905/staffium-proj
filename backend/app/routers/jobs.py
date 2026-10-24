"""Job discovery for applicants: browse/search open jobs, AI recommendations, job detail, apply."""
from fastapi import APIRouter, Depends, Query, Request

from ..auth import CurrentUser, get_current_user, require_applicant
from ..db import user_tx
from ..errors import AppError
from ..schemas import Page, paged
from ..security import limiter

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def browse_jobs(q: str | None = Query(None, max_length=200), location: str | None = Query(None, max_length=100),
                      employment_type: str | None = Query(None, pattern="^(FULL_TIME|PART_TIME|CONTRACT|INTERNSHIP)$"),
                      remote: bool | None = None, page: Page = Depends(), user: CurrentUser = Depends(get_current_user)):
    loc = ("%" + location.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%") if location else None
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT v.job_id, v.title, v.department, v.location, v.employment_type, v.is_remote, v.min_experience,
                      v.max_experience, v.min_salary, v.max_salary, v.closing_date, v.created_at, v.company_name,
                      ARRAY(SELECT s.skill_name FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
                            WHERE js.job_id = v.job_id ORDER BY js.is_required DESC, s.skill_name LIMIT 6) AS skills,
                      count(*) OVER () AS total_count
               FROM active_jobs_view v
               JOIN job_postings j ON j.job_id = v.job_id
               WHERE ($1::text IS NULL OR j.search_vector @@ websearch_to_tsquery('english', $1))
                 AND ($2::text IS NULL OR v.location ILIKE $2)
                 AND ($3::text IS NULL OR v.employment_type = $3)
                 AND ($4::boolean IS NULL OR v.is_remote = $4)
               ORDER BY CASE WHEN $1::text IS NULL THEN 0
                             ELSE ts_rank(j.search_vector, websearch_to_tsquery('english', $1)) END DESC,
                        v.created_at DESC
               LIMIT $5 OFFSET $6""",
            q or None, loc, employment_type, remote, page.limit, page.offset)
    return paged(rows, page)


@router.get("/recommended")
async def recommended_jobs(user: CurrentUser = Depends(require_applicant)):
    """Open jobs closest to the applicant's active resume embedding, with a skill check per job."""
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """WITH me AS (
                   SELECT re.embedding FROM resumes r JOIN resume_embeddings re ON re.resume_id = r.resume_id
                   WHERE r.applicant_id = $1 AND r.is_active AND r.processing_status = 'COMPLETED'
               )
               SELECT v.job_id, v.title, v.company_name, v.location, v.employment_type, v.is_remote, v.min_experience,
                      round(((1 - (je.embedding <=> me.embedding)) * 100)::numeric, 1) AS semantic_match,
                      calculate_skill_match($1, v.job_id) AS skill_match,
                      ARRAY(SELECT s.skill_name FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
                            WHERE js.job_id = v.job_id AND EXISTS (SELECT 1 FROM candidate_skills cs
                                  WHERE cs.applicant_id = $1 AND cs.skill_id = js.skill_id)) AS matched_skills,
                      ARRAY(SELECT s.skill_name FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
                            WHERE js.job_id = v.job_id AND NOT EXISTS (SELECT 1 FROM candidate_skills cs
                                  WHERE cs.applicant_id = $1 AND cs.skill_id = js.skill_id)) AS missing_skills
               FROM me
               JOIN job_embeddings je ON TRUE
               JOIN active_jobs_view v ON v.job_id = je.job_id
               WHERE NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = v.job_id AND a.applicant_id = $1)
               ORDER BY je.embedding <=> me.embedding
               LIMIT 10""",
            user.applicant_id)
        experience = await conn.fetchval("SELECT calculate_candidate_experience($1)", user.applicant_id)
    items = []
    for r in rows:
        d = dict(r)
        skill = float(d["skill_match"]) if d["skill_match"] is not None else None
        d["match"] = round(0.6 * skill + 0.4 * float(d["semantic_match"]), 1) if skill is not None else float(d["semantic_match"])
        d["meets_experience"] = float(experience or 0) >= float(d["min_experience"] or 0)
        items.append(d)
    items.sort(key=lambda x: x["match"], reverse=True)
    return {"items": items, "has_resume": bool(rows) or await _has_resume(user)}


async def _has_resume(user: CurrentUser) -> bool:
    async with user_tx(user.user_id) as conn:
        return await conn.fetchval("SELECT EXISTS (SELECT 1 FROM resumes WHERE applicant_id = $1 AND is_active AND processing_status = 'COMPLETED')",
                                   user.applicant_id)


@router.get("/{job_id}")
async def job_detail(job_id: int, user: CurrentUser = Depends(get_current_user)):
    async with user_tx(user.user_id) as conn:
        job = await conn.fetchrow(
            """SELECT j.job_id, j.title, j.department, j.description, j.location, j.employment_type, j.is_remote,
                      j.min_experience, j.max_experience, j.min_salary, j.max_salary, j.status, j.closing_date,
                      j.created_at, c.company_id, c.company_name, c.industry, c.website, c.description AS company_description
               FROM job_postings j JOIN companies c ON c.company_id = j.company_id
               WHERE j.job_id = $1 AND j.deleted_at IS NULL""", job_id)
        if job is None:
            raise AppError(404, "Job not found", "not_found")
        skills = await conn.fetch(
            """SELECT s.skill_id, s.skill_name, js.is_required, js.importance_weight,
                      ($2::bigint IS NOT NULL AND EXISTS (SELECT 1 FROM candidate_skills cs
                           WHERE cs.applicant_id = $2 AND cs.skill_id = js.skill_id)) AS i_have
               FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id
               WHERE js.job_id = $1 ORDER BY js.is_required DESC, s.skill_name""", job_id, user.applicant_id)
        mine = None
        if user.applicant_id:
            mine = await conn.fetchrow("SELECT application_id, status, applied_at FROM applications WHERE job_id = $1 AND applicant_id = $2",
                                       job_id, user.applicant_id)
    return {**dict(job), "skills": [dict(s) for s in skills], "my_application": dict(mine) if mine else None}


@router.post("/{job_id}/apply", status_code=201)
@limiter.limit("30/hour")
async def apply(request: Request, job_id: int, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        resume_id = await conn.fetchval(
            "SELECT resume_id FROM resumes WHERE applicant_id = $1 AND is_active", user.applicant_id)
        if resume_id is None:
            raise AppError(400, "Upload a resume before applying", "no_resume")
        # Triggers check the job is open and the resume is processed, bump the job's counter,
        # notify the recruiter and write the audit row - all in this one transaction.
        application_id = await conn.fetchval(
            "INSERT INTO applications (job_id, applicant_id, resume_id) VALUES ($1, $2, $3) RETURNING application_id",
            job_id, user.applicant_id, resume_id)
    return {"application_id": application_id, "status": "APPLIED"}
