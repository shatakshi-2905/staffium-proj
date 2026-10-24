"""Human review of screening decisions (see migration 007 for the rules the database enforces).

Applicants report wrongly extracted information or ask for a person to look at a decision.
Recruiters at the hiring company work through the queue and record an outcome. Every step is
audited by triggers; this module only validates input and, for re-screening, runs the scorer.
"""
from fastapi import APIRouter, Depends, Query, Request

from ..auth import CurrentUser, require_applicant, require_recruiter
from ..db import user_tx
from ..errors import AppError
from ..schemas import Page, ReviewRequestIn, ReviewResolveIn, paged
from ..security import limiter
from ..services.ranking import rank
from .recruiter import _spec_for_job, save_screening

router = APIRouter(prefix="/api", tags=["reviews"])

_LIST_COLUMNS = """rr.request_id, rr.application_id, rr.request_type, rr.field, rr.claimed_value, rr.reason, rr.status,
                   rr.outcome, rr.resolution_note, rr.score_before, rr.score_after, rr.created_at, rr.resolved_at,
                   j.job_id, j.title AS job_title, c.company_name"""


# ---------------------------------------------------------------------------------------------------
# Applicant side
# ---------------------------------------------------------------------------------------------------
@router.post("/applications/{application_id}/reviews", status_code=201)
@limiter.limit("5/hour")
async def request_review(request: Request, application_id: int, body: ReviewRequestIn, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        request_id = await conn.fetchval(
            """INSERT INTO screening_review_requests (application_id, applicant_id, request_type, field, claimed_value, reason)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING request_id""",
            application_id, user.applicant_id, body.request_type, body.field, body.claimed_value, body.reason)
    return {"request_id": request_id, "status": "OPEN"}


@router.get("/reviews")
async def my_reviews(user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            f"""SELECT {_LIST_COLUMNS}
                FROM screening_review_requests rr
                JOIN applications a ON a.application_id = rr.application_id
                JOIN job_postings j ON j.job_id = a.job_id
                JOIN companies c    ON c.company_id = j.company_id
                WHERE rr.applicant_id = $1
                ORDER BY rr.created_at DESC LIMIT 100""", user.applicant_id)
    return {"items": [dict(r) for r in rows]}


@router.post("/reviews/{request_id}/withdraw")
async def withdraw_review(request_id: int, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        result = await conn.execute(
            "UPDATE screening_review_requests SET status = 'WITHDRAWN' WHERE request_id = $1 AND applicant_id = $2",
            request_id, user.applicant_id)
    if result == "UPDATE 0":
        raise AppError(404, "Request not found", "not_found")
    return {"status": "WITHDRAWN"}


# ---------------------------------------------------------------------------------------------------
# Recruiter side
# ---------------------------------------------------------------------------------------------------
@router.get("/recruiter/reviews")
async def review_queue(status: str = Query("open", pattern="^(open|closed|all)$"), page: Page = Depends(),
                       user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:   # RLS: only this company's applications
        rows = await conn.fetch(
            f"""SELECT {_LIST_COLUMNS}, ap.first_name || ' ' || ap.last_name AS candidate_name,
                       r.first_name || ' ' || r.last_name AS reviewer_name, count(*) OVER () AS total_count
                FROM screening_review_requests rr
                JOIN applications a ON a.application_id = rr.application_id
                JOIN applicants ap  ON ap.applicant_id = rr.applicant_id
                JOIN job_postings j ON j.job_id = a.job_id
                JOIN companies c    ON c.company_id = j.company_id
                LEFT JOIN recruiters r ON r.recruiter_id = rr.reviewer_id
                WHERE ($1 = 'all' OR ($1 = 'open') = (rr.status IN ('OPEN', 'IN_REVIEW')))
                ORDER BY (rr.status = 'OPEN') DESC, rr.created_at ASC
                LIMIT $2 OFFSET $3""", status, page.limit, page.offset)
        open_count = await conn.fetchval("SELECT count(*) FROM screening_review_requests WHERE status IN ('OPEN', 'IN_REVIEW')")
    return {**paged(rows, page), "open": open_count}


@router.get("/recruiter/reviews/{request_id}")
async def review_detail(request_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rr = await conn.fetchrow(
            f"""SELECT {_LIST_COLUMNS}, rr.system_value, rr.override_score, rr.review_started_at, rr.applicant_id,
                       a.status AS application_status, ap.first_name || ' ' || ap.last_name AS candidate_name,
                       r.first_name || ' ' || r.last_name AS reviewer_name
                FROM screening_review_requests rr
                JOIN applications a ON a.application_id = rr.application_id
                JOIN applicants ap  ON ap.applicant_id = rr.applicant_id
                JOIN job_postings j ON j.job_id = a.job_id
                JOIN companies c    ON c.company_id = j.company_id
                LEFT JOIN recruiters r ON r.recruiter_id = rr.reviewer_id
                WHERE rr.request_id = $1""", request_id)
        if rr is None:
            raise AppError(404, "Request not found", "not_found")
        screening = await conn.fetchrow(
            """SELECT overall_score AS ai_score, human_score, effective_score, skill_score, experience_score, semantic_score,
                      recommendation, missing_skills, ai_explanation, llm_used, screened_at, human_note
               FROM screening_results WHERE application_id = $1""", rr["application_id"])
        # The candidate's data as it is now, to compare with the snapshot taken at submission.
        current = {
            "skills": [r[0] for r in await conn.fetch(
                """SELECT s.skill_name FROM candidate_skills cs JOIN skills s ON s.skill_id = cs.skill_id
                   WHERE cs.applicant_id = $1 ORDER BY s.skill_name""", rr["applicant_id"])],
            "experience_years": await conn.fetchval("SELECT calculate_candidate_experience($1)", rr["applicant_id"]),
        }
        history = await conn.fetch(
            """SELECT request_id, request_type, status, outcome, created_at FROM screening_review_requests
               WHERE application_id = $1 AND request_id <> $2 ORDER BY created_at DESC""", rr["application_id"], request_id)
    return {**dict(rr), "screening": dict(screening) if screening else None, "current": current,
            "history": [dict(h) for h in history]}


@router.post("/recruiter/reviews/{request_id}/start")
async def start_review(request_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        result = await conn.execute("UPDATE screening_review_requests SET status = 'IN_REVIEW' WHERE request_id = $1", request_id)
    if result == "UPDATE 0":
        raise AppError(404, "Request not found", "not_found")
    return {"status": "IN_REVIEW"}


@router.post("/recruiter/reviews/{request_id}/resolve")
@limiter.limit("30/hour")
async def resolve_review(request: Request, request_id: int, body: ReviewResolveIn, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rr = await conn.fetchrow(
            """SELECT rr.application_id, rr.status, a.job_id FROM screening_review_requests rr
               JOIN applications a ON a.application_id = rr.application_id WHERE rr.request_id = $1""", request_id)
        if rr is None:
            raise AppError(404, "Request not found", "not_found")
        if rr["status"] not in ("OPEN", "IN_REVIEW"):
            raise AppError(409, "This request is already closed", "closed")

        llm_used = None
        if body.outcome == "RESCREENED":
            # Score this one application again with the candidate's current profile. Same
            # transaction as the resolution: if anything fails, neither is recorded.
            spec = await _spec_for_job(conn, rr["job_id"], user.company_id)
            ranked, llm_used = await rank(conn, spec, pool="applications", only_application_ids=[rr["application_id"]])
            if not ranked:
                raise AppError(409, "This application can't be re-screened: its resume hasn't been processed", "not_screenable")
            await save_screening(conn, ranked, llm_used)

        row = await conn.fetchrow(
            """UPDATE screening_review_requests
               SET status = 'RESOLVED', outcome = $2, resolution_note = $3, override_score = $4
               WHERE request_id = $1
               RETURNING outcome, score_before, score_after""",
            request_id, body.outcome, body.note, body.override_score)
    return {**dict(row), "llm_used": llm_used}
