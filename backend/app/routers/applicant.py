"""Applicant profile: personal details, skills, education, experience, projects, certifications."""
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from ..auth import CurrentUser, applicant_role, require_applicant
from ..db import user_tx
from ..errors import AppError
from ..schemas import (ApplicantProfileIn, ApplicantProfilePatch, CandidateSkillIn, CertificationIn, EducationIn,
                       ExperienceIn, ProjectIn)

router = APIRouter(prefix="/api/applicant", tags=["applicant"])

# section name -> (table, primary key column, request model). Identifiers come only from this map.
SECTIONS = {
    "education": ("education", "education_id", EducationIn),
    "experience": ("experience", "experience_id", ExperienceIn),
    "projects": ("projects", "project_id", ProjectIn),
    "certifications": ("certifications", "certification_id", CertificationIn),
}
SectionName = Literal["education", "experience", "projects", "certifications"]


@router.post("/profile", status_code=201)
async def create_profile(body: ApplicantProfileIn, user: CurrentUser = Depends(applicant_role)):
    if user.applicant_id:
        raise AppError(409, "Your profile already exists", "conflict")
    data = body.model_dump()
    cols = ", ".join(data)
    params = ", ".join(f"${i + 2}" for i in range(len(data)))
    async with user_tx(user.user_id) as conn:
        applicant_id = await conn.fetchval(
            f"INSERT INTO applicants (user_id, {cols}) VALUES ($1, {params}) RETURNING applicant_id",
            user.user_id, *data.values())
    return {"applicant_id": applicant_id}


@router.get("/profile")
async def get_profile(user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        profile = await conn.fetchrow(
            """SELECT a.*, u.email, calculate_candidate_experience(a.applicant_id) AS experience_years
               FROM applicants a JOIN users u ON u.user_id = a.user_id WHERE a.applicant_id = $1""", user.applicant_id)
        skills = await conn.fetch(
            """SELECT s.skill_id, s.skill_name, s.category, cs.proficiency_level, cs.years_experience, cs.source
               FROM candidate_skills cs JOIN skills s ON s.skill_id = cs.skill_id
               WHERE cs.applicant_id = $1 ORDER BY s.skill_name""", user.applicant_id)
        education = await conn.fetch("SELECT * FROM education WHERE applicant_id = $1 ORDER BY end_year DESC NULLS FIRST", user.applicant_id)
        experience = await conn.fetch("SELECT * FROM experience WHERE applicant_id = $1 ORDER BY is_current DESC, start_date DESC", user.applicant_id)
        projects = await conn.fetch("SELECT * FROM projects WHERE applicant_id = $1 ORDER BY project_id DESC", user.applicant_id)
        certifications = await conn.fetch("SELECT * FROM certifications WHERE applicant_id = $1 ORDER BY issue_date DESC NULLS LAST", user.applicant_id)
        resume_ready = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM resumes WHERE applicant_id = $1 AND is_active AND processing_status = 'COMPLETED')",
            user.applicant_id)

    p = dict(profile)
    p.pop("user_id", None)
    checks = {
        "Headline": bool(p["headline"]),
        "Location": bool(p["location"]),
        "Phone": bool(p["phone"]),
        "A professional link": bool(p["linkedin_url"] or p["github_url"] or p["portfolio_url"]),
        "At least 3 skills": len(skills) >= 3,
        "Education": len(education) > 0,
        "Experience or a project": len(experience) + len(projects) > 0,
        "A processed resume": resume_ready,
    }
    return {
        "profile": p,
        "skills": [dict(r) for r in skills],
        "education": [dict(r) for r in education],
        "experience": [dict(r) for r in experience],
        "projects": [dict(r) for r in projects],
        "certifications": [dict(r) for r in certifications],
        "completeness": {"percent": round(100 * sum(checks.values()) / len(checks)),
                         "missing": [k for k, ok in checks.items() if not ok]},
    }


@router.patch("/profile")
async def update_profile(body: ApplicantProfilePatch, user: CurrentUser = Depends(require_applicant)):
    data = body.model_dump(exclude_unset=True)
    if not data:
        return {"updated": False}
    for required in ("first_name", "last_name", "is_discoverable"):
        if required in data and data[required] is None:
            raise AppError(422, f"{required.replace('_', ' ').capitalize()} can't be empty", "invalid")
    sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(data))
    async with user_tx(user.user_id) as conn:
        await conn.execute(f"UPDATE applicants SET {sets} WHERE applicant_id = $1", user.applicant_id, *data.values())
    return {"updated": True}


@router.put("/skills")
async def upsert_skill(body: CandidateSkillIn, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        await conn.execute(
            """INSERT INTO candidate_skills (applicant_id, skill_id, proficiency_level, years_experience, source)
               VALUES ($1, $2, $3, $4, 'MANUAL')
               ON CONFLICT (applicant_id, skill_id) DO UPDATE
               SET proficiency_level = EXCLUDED.proficiency_level, years_experience = EXCLUDED.years_experience, source = 'MANUAL'""",
            user.applicant_id, body.skill_id, body.proficiency_level, body.years_experience)
    return {"saved": True}


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(skill_id: int, user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        await conn.execute("DELETE FROM candidate_skills WHERE applicant_id = $1 AND skill_id = $2", user.applicant_id, skill_id)


@router.post("/{section}", status_code=201)
async def add_section_item(section: SectionName, body: dict, user: CurrentUser = Depends(require_applicant)):
    table, pk, model = SECTIONS[section]
    try:
        data = model.model_validate(body).model_dump()
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())
    cols = ", ".join(data)
    params = ", ".join(f"${i + 2}" for i in range(len(data)))
    async with user_tx(user.user_id) as conn:
        new_id = await conn.fetchval(
            f"INSERT INTO {table} (applicant_id, {cols}) VALUES ($1, {params}) RETURNING {pk}", user.applicant_id, *data.values())
    return {"id": new_id}


@router.delete("/{section}/{item_id}", status_code=204)
async def delete_section_item(section: SectionName, item_id: int, user: CurrentUser = Depends(require_applicant)):
    table, pk, _ = SECTIONS[section]
    async with user_tx(user.user_id) as conn:
        result = await conn.execute(f"DELETE FROM {table} WHERE {pk} = $1", item_id)   # RLS limits this to the owner's rows
    if result == "DELETE 0":
        raise AppError(404, "Not found", "not_found")


@router.get("/dashboard")
async def dashboard(user: CurrentUser = Depends(require_applicant)):
    async with user_tx(user.user_id) as conn:
        counts = await conn.fetch("SELECT status, count(*) AS n FROM applications GROUP BY status")
        upcoming = await conn.fetch(
            """SELECT i.interview_id, i.scheduled_at, i.duration_minutes, i.interview_type, i.meeting_link,
                      j.title AS job_title, c.company_name
               FROM interviews i
               JOIN applications a ON a.application_id = i.application_id
               JOIN job_postings j ON j.job_id = a.job_id
               JOIN companies c    ON c.company_id = j.company_id
               WHERE i.status = 'SCHEDULED' AND i.scheduled_at > now()
               ORDER BY i.scheduled_at LIMIT 5""")
        resume = await conn.fetchrow(
            """SELECT resume_id, file_name, version, processing_status, processing_error, uploaded_at,
                      jsonb_array_length(coalesce(parsed_data -> 'matched_skills', '[]')) AS skills_found
               FROM resumes WHERE applicant_id = $1 AND is_active""", user.applicant_id)
        pending_offers = await conn.fetchval("SELECT count(*) FROM offers WHERE offer_status = 'PENDING' AND expires_at > now()")
    return {
        "applications": {r["status"]: r["n"] for r in counts},
        "upcoming_interviews": [dict(r) for r in upcoming],
        "resume": dict(resume) if resume else None,
        "pending_offers": pending_offers,
    }
