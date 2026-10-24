"""Resume upload, versions, AI analysis and secure downloads."""
import hashlib
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile

from ..auth import CurrentUser, get_current_user, require_applicant
from ..config import get_settings
from ..db import user_tx
from ..errors import AppError
from ..security import limiter
from ..services import storage
from ..services.parsing import detect_file_type, safe_display_name
from ..services.resume_pipeline import process_resume

router = APIRouter(prefix="/api/resumes", tags=["resumes"])
log = logging.getLogger("shortlist.resumes")
S = get_settings()


@router.post("", status_code=202)
@limiter.limit(S.rate_limit_upload)
async def upload_resume(request: Request, background: BackgroundTasks, file: UploadFile = File(...),
                        user: CurrentUser = Depends(require_applicant)):
    data = await file.read(S.max_resume_bytes + 1)
    if len(data) > S.max_resume_bytes:
        raise AppError(413, f"Resumes can be at most {S.max_resume_bytes // (1024 * 1024)} MB", "too_large")
    if not data:
        raise AppError(422, "The file is empty", "invalid_file")
    file_type = detect_file_type(data)              # from the bytes, not the filename
    digest = hashlib.sha256(data).hexdigest()
    display_name = safe_display_name(file.filename, file_type)

    async with user_tx(user.user_id) as conn:
        duplicate = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM resumes WHERE applicant_id = $1 AND is_active AND content_sha256 = $2 AND processing_status <> 'FAILED')",
            user.applicant_id, digest)
    if duplicate:
        raise AppError(409, "This file is identical to your current resume", "duplicate")

    key = storage.new_object_key(user.applicant_id, file_type)
    try:
        await storage.put_resume(key, data, file_type)
    except Exception:
        log.exception("S3 upload failed")
        raise AppError(503, "File storage is unavailable right now. Please try again.", "storage_unavailable")

    try:
        async with user_tx(user.user_id) as conn:
            row = await conn.fetchrow(
                """INSERT INTO resumes (applicant_id, s3_object_key, file_name, file_type, file_size, content_sha256, version)
                   VALUES ($1, $2, $3, $4, $5, $6, 1) RETURNING resume_id, version""",
                user.applicant_id, key, display_name, file_type, len(data), digest)
            await conn.execute(
                "INSERT INTO audit_logs (user_id, action, entity_type, entity_id, ip_address) VALUES ($1, 'RESUME_UPLOADED', 'resume', $2, $3::inet)",
                user.user_id, row["resume_id"], request.client.host if request.client else None)
    except Exception:
        try:
            await storage.delete_resume(key)   # don't leave an orphaned object behind
        except Exception:
            log.warning("could not remove orphaned object %s", key)
        raise

    background.add_task(process_resume, row["resume_id"], data)
    return {"resume_id": row["resume_id"], "version": row["version"], "processing_status": "UPLOADED"}


@router.get("")
async def list_resumes(user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        rows = await conn.fetch(
            """SELECT resume_id, file_name, file_type, file_size, version, processing_status, processing_error,
                      is_active, uploaded_at, processed_at
               FROM resumes WHERE applicant_id = $1 ORDER BY version DESC""", user.applicant_id)
    return {"items": [dict(r) for r in rows]}


@router.get("/{resume_id}")
async def resume_detail(resume_id: int, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        row = await conn.fetchrow(
            """SELECT resume_id, file_name, file_type, version, processing_status, processing_error, is_active,
                      uploaded_at, processed_at, parsed_data
               FROM resumes WHERE resume_id = $1 AND applicant_id = $2""", resume_id, user.applicant_id)
        if row is None:
            raise AppError(404, "Resume not found", "not_found")
        experience_years = await conn.fetchval("SELECT calculate_candidate_experience($1)", user.applicant_id)
    out = dict(row)
    parsed = out.pop("parsed_data") or {}
    out["analysis"] = {
        "extracted_by": parsed.get("extracted_by"),
        "headline": parsed.get("headline"),
        "summary": parsed.get("summary"),
        "skills": parsed.get("matched_skills", []),
        "unrecognised_skills": [s for s in parsed.get("skills", [])
                                if s.lower() not in {m.lower() for m in parsed.get("matched_skills", [])}][:20],
        "education": parsed.get("education", []),
        "experience": parsed.get("experience", []),
        "projects": parsed.get("projects", []),
        "certifications": parsed.get("certifications", []),
        "experience_years": experience_years,
    }
    return out


@router.get("/{resume_id}/download")
async def download_resume(resume_id: int, request: Request, user: CurrentUser = Depends(get_current_user)):
    # RLS decides visibility: the owner, or a recruiter allowed to see this applicant.
    async with user_tx(user.user_id) as conn:
        row = await conn.fetchrow("SELECT s3_object_key, file_name, file_type FROM resumes WHERE resume_id = $1", resume_id)
        if row is None:
            raise AppError(404, "Resume not found", "not_found")
        await conn.execute(
            "INSERT INTO audit_logs (user_id, action, entity_type, entity_id, ip_address) VALUES ($1, 'RESUME_DOWNLOADED', 'resume', $2, $3::inet)",
            user.user_id, resume_id, request.client.host if request.client else None)
    url = storage.presigned_download(row["s3_object_key"], row["file_name"], row["file_type"])
    return {"url": url, "expires_in": storage.PRESIGN_SECONDS}
