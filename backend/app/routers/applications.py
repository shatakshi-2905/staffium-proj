"""The applicant's side of the pipeline: tracking, withdrawing, interviews, offers and feedback."""
from fastapi import APIRouter, Depends

from ..auth import CurrentUser, require_applicant
from ..db import user_tx
from ..errors import AppError
from ..schemas import OfferResponseIn, Page, paged

router = APIRouter(prefix="/api", tags=["applications"])


@router.get("/applications")
async def my_applications(page: Page = Depends(), user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT a.application_id, a.status, a.applied_at, a.status_changed_at,
                      j.job_id, j.title AS job_title, j.location, c.company_name,
                      o.offer_id, o.salary, o.currency, o.joining_date, o.expires_at, o.offer_status,
                      (cf.feedback_id IS NOT NULL) AS has_feedback,
                      count(*) OVER () AS total_count
               FROM applications a
               JOIN job_postings j ON j.job_id = a.job_id
               JOIN companies c    ON c.company_id = j.company_id
               LEFT JOIN LATERAL (SELECT * FROM offers o WHERE o.application_id = a.application_id
                                  ORDER BY o.issued_at DESC LIMIT 1) o ON TRUE
               LEFT JOIN candidate_feedback cf ON cf.application_id = a.application_id
               WHERE a.applicant_id = $1
               ORDER BY a.status_changed_at DESC
               LIMIT $2 OFFSET $3""",
            user.applicant_id, page.limit, page.offset)
    return paged(rows, page)


@router.post("/applications/{application_id}/withdraw")
async def withdraw(application_id: int, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        # The status trigger enforces which stages can be withdrawn from; RLS limits this to the owner.
        result = await conn.execute(
            "UPDATE applications SET status = 'WITHDRAWN' WHERE application_id = $1 AND applicant_id = $2",
            application_id, user.applicant_id)
    if result == "UPDATE 0":
        raise AppError(404, "Application not found", "not_found")
    return {"status": "WITHDRAWN"}


@router.get("/applications/{application_id}/feedback")
async def why_not_me(application_id: int, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        row = await conn.fetchrow(
            """SELECT cf.missing_skills, cf.match_score, cf.match_summary, cf.suggested_resources,
                      cf.suggestions_status, cf.generated_at, j.title AS job_title, c.company_name
               FROM candidate_feedback cf
               JOIN applications a ON a.application_id = cf.application_id
               JOIN job_postings j ON j.job_id = a.job_id
               JOIN companies c    ON c.company_id = j.company_id
               WHERE cf.application_id = $1 AND a.applicant_id = $2""", application_id, user.applicant_id)
    if row is None:
        raise AppError(404, "No feedback for this application", "not_found")
    return dict(row)


@router.get("/applicant/interviews")
async def my_interviews(user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT i.interview_id, i.scheduled_at, i.ends_at, i.duration_minutes, i.interview_type, i.meeting_link, i.status,
                      j.title AS job_title, c.company_name, r.first_name || ' ' || r.last_name AS interviewer_name
               FROM interviews i
               JOIN applications a ON a.application_id = i.application_id
               JOIN job_postings j ON j.job_id = a.job_id
               JOIN companies c    ON c.company_id = j.company_id
               LEFT JOIN recruiters r ON r.recruiter_id = i.interviewer_id
               WHERE a.applicant_id = $1
               ORDER BY (i.status = 'SCHEDULED' AND i.scheduled_at > now()) DESC, i.scheduled_at DESC
               LIMIT 100""", user.applicant_id)
    return {"items": [dict(r) for r in rows]}


@router.post("/offers/{offer_id}/respond")
async def respond_to_offer(offer_id: int, body: OfferResponseIn, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        # Trigger rules: only PENDING, unexpired offers; candidates may change nothing but the status.
        row = await conn.fetchrow(
            """UPDATE offers o SET offer_status = $2
               FROM applications a
               WHERE o.offer_id = $1 AND a.application_id = o.application_id AND a.applicant_id = $3
               RETURNING o.offer_status""", offer_id, body.decision, user.applicant_id)
    if row is None:
        raise AppError(404, "Offer not found", "not_found")
    return {"offer_status": row["offer_status"]}
