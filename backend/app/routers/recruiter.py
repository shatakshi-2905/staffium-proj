"""Recruiter workspace: jobs, the ranked pipeline per job, AI screening, candidate profiles and
talent search. Every query runs as the caller (user_tx), so RLS scopes it to their company."""
from fastapi import APIRouter, Depends, Query, Request

from ..auth import CurrentUser, require_recruiter
from ..config import get_settings
from ..db import user_tx, vector_literal
from ..errors import AppError
from ..schemas import JobIn, JobPatch, Page, ScreenIn, SearchIn, StatusIn, paged
from ..security import limiter
from ..services import embeddings
from ..services.ranking import JobSkill, SearchSpec, rank

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])
S = get_settings()

JOB_COLUMNS = ("title", "department", "description", "location", "employment_type", "is_remote", "min_experience",
               "max_experience", "min_salary", "max_salary", "closing_date", "status")


# ---------------------------------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------------------------------
async def _save_job_skills(conn, job_id: int, skills) -> None:
    await conn.execute("DELETE FROM job_skills WHERE job_id = $1", job_id)
    if skills:
        await conn.executemany(
            "INSERT INTO job_skills (job_id, skill_id, is_required, importance_weight) VALUES ($1, $2, $3, $4)",
            [(job_id, s.skill_id, s.is_required, s.importance_weight) for s in skills])


async def _refresh_job_embedding(conn, job_id: int) -> None:
    job = await conn.fetchrow("SELECT title, description FROM job_postings WHERE job_id = $1", job_id)
    names = [r["skill_name"] for r in await conn.fetch(
        "SELECT s.skill_name FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id WHERE js.job_id = $1", job_id)]
    vector = await embeddings.embed_passage(embeddings.job_representation(job["title"], job["description"], names))
    await conn.execute(
        """INSERT INTO job_embeddings (job_id, embedding, model_name, model_version) VALUES ($1, $2::vector, $3, $4)
           ON CONFLICT (job_id) DO UPDATE SET embedding = EXCLUDED.embedding, model_name = EXCLUDED.model_name,
               model_version = EXCLUDED.model_version, created_at = now()""",
        job_id, vector_literal(vector), embeddings.model_name(), embeddings.MODEL_VERSION)


@router.get("/jobs")
async def list_jobs(status: str | None = Query(None, pattern="^(DRAFT|OPEN|PAUSED|CLOSED)$"), page: Page = Depends(),
                    user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT j.job_id, j.title, j.department, j.location, j.employment_type, j.status, j.created_at, j.closing_date,
                      r.first_name || ' ' || r.last_name AS owner_name,
                      p.total, p.applied, p.screening, p.shortlisted, p.interview, p.offer, p.hired, p.rejected,
                      count(*) OVER () AS total_count
               FROM job_postings j
               JOIN recruitment_pipeline_view p ON p.job_id = j.job_id
               JOIN recruiters r ON r.recruiter_id = j.recruiter_id
               WHERE j.company_id = $1 AND j.deleted_at IS NULL AND ($2::text IS NULL OR j.status = $2)
               ORDER BY (j.status = 'OPEN') DESC, j.created_at DESC
               LIMIT $3 OFFSET $4""",
            user.company_id, status, page.limit, page.offset)
    return paged(rows, page)


@router.post("/jobs", status_code=201)
@limiter.limit("30/hour")
async def create_job(request: Request, body: JobIn, user: CurrentUser = Depends(require_recruiter)):
    data = body.model_dump(exclude={"skills"})
    cols = ", ".join(data)
    params = ", ".join(f"${i + 3}" for i in range(len(data)))
    async with user_tx(user.user_id) as conn:
        job_id = await conn.fetchval(
            f"INSERT INTO job_postings (company_id, recruiter_id, {cols}) VALUES ($1, $2, {params}) RETURNING job_id",
            user.company_id, user.recruiter_id, *data.values())
        await _save_job_skills(conn, job_id, body.skills)
        await _refresh_job_embedding(conn, job_id)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        job = await conn.fetchrow("SELECT * FROM job_postings WHERE job_id = $1 AND company_id = $2 AND deleted_at IS NULL",
                                  job_id, user.company_id)
        if job is None:
            raise AppError(404, "Job not found", "not_found")
        skills = await conn.fetch(
            """SELECT s.skill_id, s.skill_name, js.is_required, js.importance_weight
               FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id WHERE js.job_id = $1
               ORDER BY js.is_required DESC, s.skill_name""", job_id)
        pipeline = await conn.fetch("SELECT * FROM get_application_pipeline($1)", job_id)
    j = dict(job)
    j.pop("search_vector", None)
    return {**j, "skills": [dict(s) for s in skills], "pipeline": {r["status"]: r["total"] for r in pipeline}}


@router.patch("/jobs/{job_id}")
async def update_job(job_id: int, body: JobPatch, user: CurrentUser = Depends(require_recruiter)):
    data = {k: v for k, v in body.model_dump(exclude_unset=True, exclude={"skills"}).items() if k in JOB_COLUMNS}
    for required in ("title", "description", "employment_type", "is_remote", "min_experience", "status"):
        if required in data and data[required] is None:
            raise AppError(422, f"{required.replace('_', ' ').capitalize()} can't be empty", "invalid")
    async with user_tx(user.user_id) as conn:
        if data:
            sets = ", ".join(f"{col} = ${i + 3}" for i, col in enumerate(data))
            result = await conn.execute(
                f"UPDATE job_postings SET {sets} WHERE job_id = $1 AND company_id = $2 AND deleted_at IS NULL",
                job_id, user.company_id, *data.values())
            if result == "UPDATE 0":
                raise AppError(404, "Job not found", "not_found")
        elif not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM job_postings WHERE job_id = $1 AND company_id = $2 AND deleted_at IS NULL)",
                                     job_id, user.company_id):
            raise AppError(404, "Job not found", "not_found")
        if body.skills is not None:
            if len({s.skill_id for s in body.skills}) != len(body.skills):
                raise AppError(422, "A skill is listed twice", "invalid")
            await _save_job_skills(conn, job_id, body.skills)
        if body.skills is not None or {"title", "description"} & data.keys():
            await _refresh_job_embedding(conn, job_id)
    return {"updated": True}


@router.delete("/jobs/{job_id}", status_code=204)
async def archive_job(job_id: int, user: CurrentUser = Depends(require_recruiter)):
    """Soft delete: the job disappears from listings, but every application, interview and offer
    attached to it is kept."""
    async with user_tx(user.user_id) as conn:
        result = await conn.execute(
            "UPDATE job_postings SET deleted_at = now() WHERE job_id = $1 AND company_id = $2 AND deleted_at IS NULL",
            job_id, user.company_id)
    if result == "UPDATE 0":
        raise AppError(404, "Job not found", "not_found")


# ---------------------------------------------------------------------------------------------------
# Pipeline for one job
# ---------------------------------------------------------------------------------------------------
@router.get("/jobs/{job_id}/applications")
async def job_applications(job_id: int, status: str | None = Query(None, pattern="^(APPLIED|SCREENING|SHORTLISTED|INTERVIEW|OFFER|HIRED|REJECTED|WITHDRAWN)$"),
                           page: Page = Depends(), user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT a.application_id, a.applicant_id, a.status, a.applied_at, a.status_changed_at,
                      ap.first_name || ' ' || ap.last_name AS candidate_name, ap.headline, ap.location,
                      calculate_candidate_experience(a.applicant_id) AS experience_years,
                      sr.overall_score, sr.skill_score, sr.experience_score, sr.semantic_score, sr.education_score,
                      sr.recommendation, sr.missing_skills, sr.llm_used, sr.screened_at,
                      CASE WHEN sr.overall_score IS NOT NULL
                           THEN RANK() OVER (ORDER BY sr.overall_score DESC NULLS LAST) END AS rank_in_job,
                      count(*) OVER () AS total_count
               FROM applications a
               JOIN applicants ap ON ap.applicant_id = a.applicant_id
               LEFT JOIN screening_results sr ON sr.application_id = a.application_id
               WHERE a.job_id = $1 AND ($2::text IS NULL OR a.status = $2)
               ORDER BY sr.overall_score DESC NULLS LAST, a.applied_at
               LIMIT $3 OFFSET $4""",
            job_id, status, page.limit, page.offset)
    return paged(rows, page)


async def _spec_for_job(conn, job_id: int, company_id: int) -> SearchSpec:
    job = await conn.fetchrow("SELECT job_id, title, description, min_experience FROM job_postings WHERE job_id = $1 AND company_id = $2",
                              job_id, company_id)
    if job is None:
        raise AppError(404, "Job not found", "not_found")
    skills = await conn.fetch(
        """SELECT js.skill_id, s.skill_name, js.is_required, js.importance_weight
           FROM job_skills js JOIN skills s ON s.skill_id = js.skill_id WHERE js.job_id = $1""", job_id)
    return SearchSpec(title=job["title"], description=job["description"], job_id=job_id,
                      min_experience=float(job["min_experience"] or 0),
                      skills=[JobSkill(r["skill_id"], r["skill_name"], r["is_required"], float(r["importance_weight"])) for r in skills])


@router.post("/jobs/{job_id}/screen")
@limiter.limit(S.rate_limit_search)
async def screen_applicants(request: Request, job_id: int, body: ScreenIn | None = None,
                            user: CurrentUser = Depends(require_recruiter)):
    """Rank everyone who applied. All applicants get a database-derived score; the top 20 by
    retrieval also get an AI score and explanation."""
    use_ai = body.use_ai if body else True
    async with user_tx(user.user_id) as conn:
        spec = await _spec_for_job(conn, job_id, user.company_id)
        ranked, llm_used = await rank(conn, spec, pool="applications", use_llm=use_ai)
        model = S.bedrock_model_id if llm_used else None
        for c in ranked:
            await conn.execute(
                """INSERT INTO screening_results (application_id, semantic_score, skill_score, experience_score, education_score,
                       overall_score, recommendation, strengths, missing_skills, ai_explanation, llm_used, model_name, model_version)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                   ON CONFLICT (application_id) DO UPDATE SET semantic_score = EXCLUDED.semantic_score,
                       skill_score = EXCLUDED.skill_score, experience_score = EXCLUDED.experience_score,
                       education_score = EXCLUDED.education_score, overall_score = EXCLUDED.overall_score,
                       recommendation = EXCLUDED.recommendation, strengths = EXCLUDED.strengths,
                       missing_skills = EXCLUDED.missing_skills, ai_explanation = EXCLUDED.ai_explanation,
                       llm_used = EXCLUDED.llm_used, model_name = EXCLUDED.model_name,
                       model_version = EXCLUDED.model_version, screened_at = now()""",
                c["application_id"], c["semantic_score"], c["skill_score"], c["experience_score"], c["education_score"],
                c["overall_score"], c["recommendation"], c["strengths"], c["missing_skills"], c["ai_explanation"],
                c["llm_used"], model or embeddings.model_name(), "llm" if c["llm_used"] else "deterministic")
        moved = await conn.execute("UPDATE applications SET status = 'SCREENING' WHERE job_id = $1 AND status = 'APPLIED'", job_id)
    return {"screened": len(ranked), "llm_used": llm_used, "moved_to_screening": int(moved.split()[-1])}


@router.patch("/applications/{application_id}/status")
async def change_status(application_id: int, body: StatusIn, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        result = await conn.execute("UPDATE applications SET status = $2 WHERE application_id = $1", application_id, body.status)
    if result == "UPDATE 0":
        raise AppError(404, "Application not found", "not_found")
    return {"status": body.status}


# ---------------------------------------------------------------------------------------------------
# Candidate profiles
# ---------------------------------------------------------------------------------------------------
async def _candidate_profile(conn, applicant_id: int) -> dict:
    profile = await conn.fetchrow(
        """SELECT a.applicant_id, a.first_name, a.last_name, a.headline, a.location, a.phone, a.linkedin_url, a.github_url,
                  a.portfolio_url, a.notice_period_days, u.email, calculate_candidate_experience(a.applicant_id) AS experience_years
           FROM applicants a LEFT JOIN users u ON u.user_id = a.user_id WHERE a.applicant_id = $1""", applicant_id)
    if profile is None:
        raise AppError(404, "Candidate not found", "not_found")
    q = {
        "skills": """SELECT s.skill_id, s.skill_name, s.category, cs.proficiency_level, cs.years_experience, cs.source
                     FROM candidate_skills cs JOIN skills s ON s.skill_id = cs.skill_id WHERE cs.applicant_id = $1 ORDER BY s.skill_name""",
        "experience": "SELECT company_name, designation, start_date, end_date, is_current, description FROM experience WHERE applicant_id = $1 ORDER BY start_date DESC",
        "education": "SELECT institution, degree, field_of_study, start_year, end_year, cgpa FROM education WHERE applicant_id = $1 ORDER BY end_year DESC NULLS FIRST",
        "projects": "SELECT title, description, technologies, project_url FROM projects WHERE applicant_id = $1 ORDER BY project_id DESC",
        "certifications": "SELECT name, issuer, issue_date, credential_url FROM certifications WHERE applicant_id = $1",
    }
    out = {"profile": dict(profile)}
    for key, sql in q.items():
        out[key] = [dict(r) for r in await conn.fetch(sql, applicant_id)]
    resume = await conn.fetchrow(
        "SELECT resume_id, file_name, version, uploaded_at FROM resumes WHERE applicant_id = $1 AND is_active AND processing_status = 'COMPLETED'",
        applicant_id)
    out["resume"] = dict(resume) if resume else None
    return out


@router.get("/applications/{application_id}")
async def application_detail(application_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        app = await conn.fetchrow(
            """SELECT a.application_id, a.applicant_id, a.job_id, a.resume_id, a.status, a.applied_at, a.status_changed_at,
                      j.title AS job_title
               FROM applications a JOIN job_postings j ON j.job_id = a.job_id WHERE a.application_id = $1""", application_id)
        if app is None:
            raise AppError(404, "Application not found", "not_found")
        out = await _candidate_profile(conn, app["applicant_id"])
        out["resume"] = {"resume_id": app["resume_id"]}   # the exact resume submitted with this application
        screening = await conn.fetchrow("SELECT * FROM screening_results WHERE application_id = $1", application_id)
        interviews = await conn.fetch(
            """SELECT i.*, r.first_name || ' ' || r.last_name AS interviewer_name,
                      (SELECT json_agg(f ORDER BY f.submitted_at) FROM (
                           SELECT f.technical_score, f.communication_score, f.problem_solving_score, f.overall_score,
                                  f.recommendation, f.comments, f.submitted_at, r2.first_name || ' ' || r2.last_name AS reviewer
                           FROM interview_feedback f JOIN recruiters r2 ON r2.recruiter_id = f.interviewer_id
                           WHERE f.interview_id = i.interview_id) f) AS feedback
               FROM interviews i JOIN recruiters r ON r.recruiter_id = i.interviewer_id
               WHERE i.application_id = $1 ORDER BY i.scheduled_at DESC""", application_id)
        offers = await conn.fetch("SELECT * FROM offers WHERE application_id = $1 ORDER BY issued_at DESC", application_id)
        allowed = await conn.fetch(
            "SELECT to_status FROM application_status_transitions WHERE from_status = $1 AND actor = 'RECRUITER'", app["status"])
    out.update(application=dict(app), screening=dict(screening) if screening else None,
               interviews=[dict(i) for i in interviews], offers=[dict(o) for o in offers],
               allowed_transitions=[r["to_status"] for r in allowed])
    return out


@router.get("/candidates/{applicant_id}")
async def candidate_detail(applicant_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        out = await _candidate_profile(conn, applicant_id)   # RLS: discoverable or applied to this company
        out["applications"] = [dict(r) for r in await conn.fetch(
            """SELECT a.application_id, a.status, j.job_id, j.title FROM applications a JOIN job_postings j ON j.job_id = a.job_id
               WHERE a.applicant_id = $1 ORDER BY a.applied_at DESC""", applicant_id)]
    return out


# ---------------------------------------------------------------------------------------------------
# Talent search (the hero feature)
# ---------------------------------------------------------------------------------------------------
@router.post("/search")
@limiter.limit(S.rate_limit_search)
async def talent_search(request: Request, body: SearchIn, user: CurrentUser = Depends(require_recruiter)):
    f = body.filters
    async with user_tx(user.user_id) as conn:
        if body.job_id:
            spec = await _spec_for_job(conn, body.job_id, user.company_id)
            spec.description = body.description            # the recruiter may have edited the text
            spec.title = body.title or spec.title
            spec.job_id = None
        else:
            spec = SearchSpec(title=body.title, description=body.description)
            if f.must_have_skill_ids:
                rows = await conn.fetch("SELECT skill_id, skill_name FROM skills WHERE skill_id = ANY($1::bigint[])", f.must_have_skill_ids)
                spec.skills = [JobSkill(r["skill_id"], r["skill_name"], True, 1.0) for r in rows]
        spec.must_have_skill_ids = f.must_have_skill_ids
        if f.min_experience is not None:
            spec.min_experience = float(f.min_experience)
        spec.location, spec.max_notice_days = f.location, f.max_notice_days

        ranked, llm_used = await rank(conn, spec, pool="talent", use_llm=body.use_ai)
        results = [{k: c[k] for k in ("applicant_id", "name", "headline", "location", "experience_years", "overall_score",
                                       "skill_score", "experience_score", "semantic_score", "education_score",
                                       "recommendation", "strengths", "missing_skills", "matched_skills", "ai_explanation", "llm_used")}
                   for c in ranked[:50]]
        search_id = await conn.fetchval(
            """INSERT INTO search_history (recruiter_id, job_id, query, filters, results, results_count, llm_used)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING search_id""",
            user.recruiter_id, body.job_id, body.description, f.model_dump(mode="json") | {"title": body.title},
            results, len(results), llm_used)
    return {"search_id": search_id, "llm_used": llm_used, "items": results}


@router.get("/search/history")
async def search_history(page: Page = Depends(), user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT h.search_id, h.job_id, j.title AS job_title, left(h.query, 240) AS query, h.filters, h.results_count,
                      h.llm_used, h.created_at, count(*) OVER () AS total_count
               FROM search_history h LEFT JOIN job_postings j ON j.job_id = h.job_id
               ORDER BY h.created_at DESC LIMIT $1 OFFSET $2""", page.limit, page.offset)
    return paged(rows, page)


@router.get("/search/history/{search_id}")
async def search_history_item(search_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        row = await conn.fetchrow("SELECT * FROM search_history WHERE search_id = $1", search_id)
    if row is None:
        raise AppError(404, "Search not found", "not_found")
    return dict(row)
