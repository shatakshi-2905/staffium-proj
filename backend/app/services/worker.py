"""In-process background worker. Every few seconds it:

  1. delivers due notifications_outbox rows through SES (lease + retry with backoff)
  2. expires offers past their deadline
  3. fills "why not me" learning suggestions for new rejections
  4. retries resumes stuck in UPLOADED/PROCESSING (e.g. the instance restarted mid-processing)

Running it inside the API process avoids extra infrastructure. It is safe with several API
instances because work is claimed with FOR UPDATE SKIP LOCKED.
"""
import asyncio
import logging
from urllib.parse import quote_plus

from ..config import get_settings
from ..db import system_tx
from . import llm, mailer
from .resume_pipeline import MAX_ATTEMPTS, process_resume

log = logging.getLogger("shortlist.worker")
MAX_EMAIL_ATTEMPTS = 5


async def deliver_outbox(batch: int = 20) -> int:
    s = get_settings()
    async with system_tx() as conn:
        rows = [dict(r) for r in await conn.fetch("SELECT * FROM app.claim_outbox($1)", batch)]
    for row in rows:
        if not s.email_enabled:
            await _mark(row["outbox_id"], "SKIPPED", "email disabled")
            continue
        try:
            await mailer.send(row)
            await _mark(row["outbox_id"], "SENT", None)
        except Exception as exc:
            final = row["attempts"] >= MAX_EMAIL_ATTEMPTS
            log.warning("email %s failed (attempt %s): %s", row["outbox_id"], row["attempts"], type(exc).__name__)
            await _mark(row["outbox_id"], "FAILED" if final else "PENDING", f"{type(exc).__name__}: {exc}"[:500])
    return len(rows)


async def _mark(outbox_id: int, status: str, error: str | None) -> None:
    async with system_tx() as conn:
        await conn.execute(
            """UPDATE notifications_outbox SET status = $2::varchar, last_error = $3,
                      sent_at = CASE WHEN $2::varchar = 'SENT' THEN now() ELSE sent_at END
               WHERE outbox_id = $1""",
            outbox_id, status, error)


def _fallback_suggestion(skill: str) -> dict:
    return {"skill": skill,
            "suggestion": f"Build a small project that uses {skill} end to end, and complete an introductory course on it.",
            "search_url": f"https://www.coursera.org/search?query={quote_plus(skill)}"}


async def fill_feedback_suggestions(batch: int = 5) -> int:
    async with system_tx() as conn:
        rows = await conn.fetch(
            """SELECT cf.feedback_id, cf.missing_skills, j.title
               FROM candidate_feedback cf
               JOIN applications a ON a.application_id = cf.application_id
               JOIN job_postings j ON j.job_id = a.job_id
               WHERE cf.suggestions_status = 'PENDING'
               ORDER BY cf.generated_at LIMIT $1
               FOR UPDATE OF cf SKIP LOCKED""",
            batch)
        for r in rows:
            skills = [str(s) for s in (r["missing_skills"] or [])][:8]
            suggestions = (await llm.suggest_resources(r["title"], skills)) if skills else []
            by_skill = {x["skill"]: x for x in (suggestions or [])}
            final = []
            for skill in skills:
                item = by_skill.get(skill) or _fallback_suggestion(skill)
                item.setdefault("search_url", f"https://www.coursera.org/search?query={quote_plus(skill)}")
                final.append(item)
            await conn.execute(
                "UPDATE candidate_feedback SET suggested_resources = $2, suggestions_status = 'READY' WHERE feedback_id = $1",
                r["feedback_id"], final)
    return len(rows)


async def retry_stuck_resumes() -> int:
    async with system_tx() as conn:
        ids = [r["resume_id"] for r in await conn.fetch(
            """SELECT resume_id FROM resumes
               WHERE processing_status IN ('UPLOADED', 'PROCESSING')
                 AND uploaded_at < now() - interval '3 minutes' AND processing_attempts < $1
               ORDER BY uploaded_at LIMIT 3""", MAX_ATTEMPTS)]
        # Anything that has exhausted its attempts is marked failed so the applicant can re-upload.
        await conn.execute(
            """UPDATE resumes SET processing_status = 'FAILED',
                      processing_error = coalesce(processing_error, 'We couldn''t process this file. Please upload it again.')
               WHERE processing_status IN ('UPLOADED', 'PROCESSING') AND processing_attempts >= $1
                 AND uploaded_at < now() - interval '3 minutes'""", MAX_ATTEMPTS)
    for rid in ids:
        await process_resume(rid)
    return len(ids)


async def run_once() -> None:
    for step in (deliver_outbox, fill_feedback_suggestions, retry_stuck_resumes):
        try:
            await step()
        except Exception:
            log.exception("worker step %s failed", step.__name__)
    try:
        async with system_tx() as conn:
            expired = await conn.fetchval("SELECT app.expire_stale_offers()")
        if expired:
            log.info("expired %s offers", expired)
    except Exception:
        log.exception("offer expiry failed")


async def run_forever() -> None:
    interval = get_settings().worker_interval_seconds
    while True:
        await run_once()
        await asyncio.sleep(interval)
