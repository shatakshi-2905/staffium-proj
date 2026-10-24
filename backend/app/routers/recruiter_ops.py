"""Recruiter operations: interview scheduling and feedback, offers, analytics."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..auth import CurrentUser, require_recruiter
from ..db import user_tx
from ..errors import AppError
from ..schemas import InterviewFeedbackIn, InterviewIn, InterviewPatch, OfferIn, Page, paged

router = APIRouter(prefix="/api/recruiter", tags=["recruiter"])


# ---------------------------------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------------------------------
@router.get("/interviews")
async def list_interviews(scope: str = Query("upcoming", pattern="^(upcoming|past|all)$"), mine: bool = False,
                          page: Page = Depends(), user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT i.interview_id, i.application_id, i.scheduled_at, i.ends_at, i.duration_minutes, i.interview_type,
                      i.meeting_link, i.status, i.notes, i.interviewer_id, r.first_name || ' ' || r.last_name AS interviewer_name,
                      ap.first_name || ' ' || ap.last_name AS candidate_name, j.job_id, j.title AS job_title,
                      EXISTS (SELECT 1 FROM interview_feedback f WHERE f.interview_id = i.interview_id) AS has_feedback,
                      count(*) OVER () AS total_count
               FROM interviews i
               JOIN recruiters r   ON r.recruiter_id = i.interviewer_id
               JOIN applications a ON a.application_id = i.application_id
               JOIN applicants ap  ON ap.applicant_id = a.applicant_id
               JOIN job_postings j ON j.job_id = a.job_id
               WHERE ($1 = 'all'
                      OR ($1 = 'upcoming' AND i.status = 'SCHEDULED' AND i.ends_at > now())
                      OR ($1 = 'past' AND (i.status <> 'SCHEDULED' OR i.ends_at <= now())))
                 AND (NOT $2 OR i.interviewer_id = $3)
               ORDER BY CASE WHEN $1 = 'upcoming' THEN i.scheduled_at END ASC, i.scheduled_at DESC
               LIMIT $4 OFFSET $5""",
            scope, mine, user.recruiter_id, page.limit, page.offset)
    return paged(rows, page)


@router.post("/interviews", status_code=201)
async def schedule_interview(body: InterviewIn, user: CurrentUser = Depends(require_recruiter)):
    # Triggers validate: future time, shortlisted candidate, interviewer from the same company.
    # The exclusion constraints reject double-booking the interviewer or the candidate.
    async with user_tx(user.user_id) as conn:
        interview_id = await conn.fetchval(
            """INSERT INTO interviews (application_id, interviewer_id, scheduled_at, duration_minutes, interview_type, meeting_link, notes)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING interview_id""",
            body.application_id, body.interviewer_id or user.recruiter_id, body.scheduled_at, body.duration_minutes,
            body.interview_type, body.meeting_link, body.notes)
    return {"interview_id": interview_id}


@router.patch("/interviews/{interview_id}")
async def update_interview(interview_id: int, body: InterviewPatch, user: CurrentUser = Depends(require_recruiter)):
    data = body.model_dump(exclude_unset=True)
    for required in ("scheduled_at", "duration_minutes", "interviewer_id", "interview_type", "status"):
        if required in data and data[required] is None:
            raise AppError(422, f"{required.replace('_', ' ').capitalize()} can't be empty", "invalid")
    if not data:
        return {"updated": False}
    sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(data))
    async with user_tx(user.user_id) as conn:
        result = await conn.execute(f"UPDATE interviews SET {sets} WHERE interview_id = $1", interview_id, *data.values())
    if result == "UPDATE 0":
        raise AppError(404, "Interview not found", "not_found")
    return {"updated": True}


@router.post("/interviews/{interview_id}/feedback", status_code=201)
async def add_feedback(interview_id: int, body: InterviewFeedbackIn, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM interviews WHERE interview_id = $1)", interview_id):
            raise AppError(404, "Interview not found", "not_found")
        feedback_id = await conn.fetchval(
            """INSERT INTO interview_feedback (interview_id, interviewer_id, technical_score, communication_score,
                   problem_solving_score, overall_score, recommendation, comments)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING feedback_id""",
            interview_id, user.recruiter_id, body.technical_score, body.communication_score,
            body.problem_solving_score, body.overall_score, body.recommendation, body.comments)
    return {"feedback_id": feedback_id}


# ---------------------------------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------------------------------
@router.get("/offers")
async def list_offers(page: Page = Depends(), user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT o.offer_id, o.application_id, o.salary, o.currency, o.joining_date, o.offer_status, o.issued_at,
                      o.expires_at, o.responded_at, ap.first_name || ' ' || ap.last_name AS candidate_name,
                      j.job_id, j.title AS job_title, count(*) OVER () AS total_count
               FROM offers o
               JOIN applications a ON a.application_id = o.application_id
               JOIN applicants ap  ON ap.applicant_id = a.applicant_id
               JOIN job_postings j ON j.job_id = a.job_id
               ORDER BY o.issued_at DESC LIMIT $1 OFFSET $2""", page.limit, page.offset)
    return paged(rows, page)


@router.post("/offers", status_code=201)
async def make_offer(body: OfferIn, user: CurrentUser = Depends(require_recruiter)):
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    async with user_tx(user.user_id) as conn:
        offer_id = await conn.fetchval(
            """INSERT INTO offers (application_id, salary, currency, joining_date, expires_at, notes)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING offer_id""",
            body.application_id, body.salary, body.currency, body.joining_date, expires_at, body.notes)
    return {"offer_id": offer_id, "expires_at": expires_at}


@router.post("/offers/{offer_id}/withdraw")
async def withdraw_offer(offer_id: int, user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        result = await conn.execute("UPDATE offers SET offer_status = 'EXPIRED' WHERE offer_id = $1", offer_id)
    if result == "UPDATE 0":
        raise AppError(404, "Offer not found", "not_found")
    return {"offer_status": "EXPIRED"}


# ---------------------------------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------------------------------
@router.get("/analytics")
async def analytics(days: int = Query(30, ge=7, le=180), user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        kpis = await conn.fetchrow(
            """SELECT
                   (SELECT count(*) FROM job_postings WHERE company_id = $1 AND status = 'OPEN' AND deleted_at IS NULL) AS active_jobs,
                   count(DISTINCT a.applicant_id) AS candidates,
                   count(a.application_id) AS applications,
                   count(a.application_id) FILTER (WHERE a.status = 'SHORTLISTED') AS shortlisted,
                   (SELECT count(*) FROM interviews i WHERE i.status = 'SCHEDULED' AND i.scheduled_at > now()
                        AND app.application_company(i.application_id) = $1) AS upcoming_interviews,
                   (SELECT count(*) FROM offers o WHERE o.offer_status = 'PENDING'
                        AND app.application_company(o.application_id) = $1) AS pending_offers,
                   count(a.application_id) FILTER (WHERE a.status = 'HIRED') AS hires
               FROM applications a JOIN job_postings j ON j.job_id = a.job_id
               WHERE j.company_id = $1""", user.company_id)

        # Funnel: how many applications ever reached each stage (a hired candidate passed every stage).
        funnel = await conn.fetchrow(
            """SELECT count(*) AS applied,
                      count(*) FILTER (WHERE a.status IN ('SCREENING','SHORTLISTED','INTERVIEW','OFFER','HIRED')
                                          OR EXISTS (SELECT 1 FROM screening_results sr WHERE sr.application_id = a.application_id)) AS screened,
                      count(*) FILTER (WHERE a.status IN ('SHORTLISTED','INTERVIEW','OFFER','HIRED')
                                          OR EXISTS (SELECT 1 FROM interviews i WHERE i.application_id = a.application_id)) AS shortlisted,
                      count(*) FILTER (WHERE a.status IN ('INTERVIEW','OFFER','HIRED')
                                          OR EXISTS (SELECT 1 FROM interviews i WHERE i.application_id = a.application_id)) AS interviewed,
                      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM offers o WHERE o.application_id = a.application_id)) AS offered,
                      count(*) FILTER (WHERE a.status = 'HIRED') AS hired
               FROM applications a JOIN job_postings j ON j.job_id = a.job_id WHERE j.company_id = $1""", user.company_id)

        timeline = await conn.fetch(
            """SELECT d::date AS day, count(a.application_id) AS applications
               FROM generate_series(current_date - ($2::int - 1), current_date, interval '1 day') AS d
               LEFT JOIN applications a ON a.applied_at::date = d::date
                    AND a.job_id IN (SELECT job_id FROM job_postings WHERE company_id = $1)
               GROUP BY d ORDER BY d""", user.company_id, days)

        by_job = await conn.fetch(
            """SELECT j.job_id, j.title, count(sr.screening_id) AS screened, round(avg(sr.effective_score), 1) AS avg_score,
                      max(sr.effective_score) AS best_score
               FROM job_postings j
               JOIN applications a ON a.job_id = j.job_id
               JOIN screening_results sr ON sr.application_id = a.application_id
               WHERE j.company_id = $1 AND j.deleted_at IS NULL
               GROUP BY j.job_id ORDER BY avg_score DESC NULLS LAST LIMIT 8""", user.company_id)

        top_skills = await conn.fetch(
            """SELECT s.skill_name, count(DISTINCT cs.applicant_id) AS candidates
               FROM applications a
               JOIN job_postings j     ON j.job_id = a.job_id
               JOIN candidate_skills cs ON cs.applicant_id = a.applicant_id
               JOIN skills s           ON s.skill_id = cs.skill_id
               WHERE j.company_id = $1
               GROUP BY s.skill_name ORDER BY candidates DESC, s.skill_name LIMIT 10""", user.company_id)

        mine = await conn.fetchrow("SELECT * FROM get_recruiter_statistics($1)", user.recruiter_id)
    return {"kpis": dict(kpis), "funnel": dict(funnel), "timeline": [dict(r) for r in timeline],
            "by_job": [dict(r) for r in by_job], "top_skills": [dict(r) for r in top_skills], "mine": dict(mine)}
