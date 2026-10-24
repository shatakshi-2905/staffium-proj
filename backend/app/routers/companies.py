"""Recruiter onboarding and company settings.

Joining a company requires that company's invite code (random, 24 chars, rotatable by an admin).
Company creation and joining run on the system connection because a new recruiter has no company
yet for RLS to scope against; both are fully validated here first.
"""
import secrets

from fastapi import APIRouter, Depends, Request

from ..auth import CurrentUser, recruiter_role, require_company_admin, require_recruiter
from ..db import system_tx, user_tx
from ..errors import AppError
from ..schemas import CompanyCreateIn, CompanyPatch, JoinCompanyIn
from ..security import limiter

router = APIRouter(prefix="/api", tags=["companies"])


def _invite_code() -> str:
    return secrets.token_urlsafe(18)   # 24 characters, ~144 bits


@router.post("/companies", status_code=201)
@limiter.limit("5/hour")
async def create_company(request: Request, body: CompanyCreateIn, user: CurrentUser = Depends(recruiter_role)):
    if user.recruiter_id:
        raise AppError(409, "You already belong to a company", "conflict")
    async with system_tx() as conn:
        company_id = await conn.fetchval(
            """INSERT INTO companies (company_name, industry, description, location, website, created_by)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING company_id""",
            body.company_name, body.industry, body.description, body.location, body.website, user.user_id)
        await conn.execute("INSERT INTO company_invites (company_id, invite_code) VALUES ($1, $2)", company_id, _invite_code())
        await conn.execute(
            """INSERT INTO recruiters (user_id, company_id, first_name, last_name, designation, is_company_admin)
               VALUES ($1, $2, $3, $4, $5, TRUE)""",
            user.user_id, company_id, body.first_name, body.last_name, body.designation)
        await conn.execute("INSERT INTO audit_logs (user_id, action, entity_type, entity_id) VALUES ($1, 'COMPANY_CREATED', 'company', $2)",
                           user.user_id, company_id)
    return {"company_id": company_id}


@router.post("/companies/join", status_code=201)
@limiter.limit("5/minute")
async def join_company(request: Request, body: JoinCompanyIn, user: CurrentUser = Depends(recruiter_role)):
    if user.recruiter_id:
        raise AppError(409, "You already belong to a company", "conflict")
    async with system_tx() as conn:
        company_id = await conn.fetchval("SELECT company_id FROM company_invites WHERE invite_code = $1", body.invite_code)
        if company_id is None:
            raise AppError(404, "That invite code isn't valid", "invalid_invite")
        await conn.execute(
            "INSERT INTO recruiters (user_id, company_id, first_name, last_name, designation) VALUES ($1, $2, $3, $4, $5)",
            user.user_id, company_id, body.first_name, body.last_name, body.designation)
        await conn.execute("INSERT INTO audit_logs (user_id, action, entity_type, entity_id) VALUES ($1, 'COMPANY_JOINED', 'company', $2)",
                           user.user_id, company_id)
    return {"company_id": company_id}


@router.get("/company")
async def my_company(user: CurrentUser = Depends(require_recruiter)):
    async with user_tx(user.user_id) as conn:
        company = await conn.fetchrow("SELECT * FROM companies WHERE company_id = $1", user.company_id)
        team = await conn.fetch(
            """SELECT r.recruiter_id, r.first_name, r.last_name, r.designation, r.is_company_admin, u.email, r.created_at
               FROM recruiters r JOIN users u ON u.user_id = r.user_id
               WHERE r.company_id = $1 ORDER BY r.created_at""", user.company_id)
        invite = await conn.fetchval("SELECT invite_code FROM company_invites WHERE company_id = $1", user.company_id)  # admins only (RLS)
    c = dict(company)
    c.pop("created_by", None)
    return {"company": c, "team": [dict(t) for t in team], "invite_code": invite,
            "me": {"recruiter_id": user.recruiter_id, "is_company_admin": user.is_company_admin}}


@router.patch("/company")
async def update_company(body: CompanyPatch, user: CurrentUser = Depends(require_company_admin)):
    data = body.model_dump(exclude_unset=True)
    if "company_name" in data and not data["company_name"]:
        raise AppError(422, "Company name can't be empty", "invalid")
    if data:
        sets = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(data))
        async with user_tx(user.user_id) as conn:
            await conn.execute(f"UPDATE companies SET {sets} WHERE company_id = $1", user.company_id, *data.values())
    return {"updated": bool(data)}


@router.post("/company/invite/rotate")
async def rotate_invite(user: CurrentUser = Depends(require_company_admin)):
    code = _invite_code()
    async with system_tx() as conn:
        await conn.execute("UPDATE company_invites SET invite_code = $2, rotated_at = now() WHERE company_id = $1", user.company_id, code)
        await conn.execute("INSERT INTO audit_logs (user_id, action, entity_type, entity_id) VALUES ($1, 'INVITE_ROTATED', 'company', $2)",
                           user.user_id, user.company_id)
    return {"invite_code": code}
