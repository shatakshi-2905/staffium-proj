"""Authentication (who is calling) and role-based authorization (what they may call).

Tokens are Supabase Auth JWTs. They are verified locally on every request:
  * asymmetric projects (ES256/RS256): signature checked against Supabase's JWKS endpoint
  * legacy projects (HS256): checked with SUPABASE_JWT_SECRET
plus issuer, audience and expiry. The role is NOT read from the token: it is looked up in
public.users, so editing one's own auth metadata can never grant a different role.
"""
import asyncio
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import system_tx
from .errors import AppError

_bearer = HTTPBearer(auto_error=False)
_jwks_client: jwt.PyJWKClient | None = None


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    email: str
    role: str
    applicant_id: int | None = None
    recruiter_id: int | None = None
    company_id: int | None = None
    is_company_admin: bool = False


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        s = get_settings()
        _jwks_client = jwt.PyJWKClient(f"{s.supabase_issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=5)
    return _jwks_client


def verify_token(token: str) -> dict:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise AppError(401, "Invalid session", "invalid_token")
    alg = header.get("alg")
    if alg == "HS256":
        if not s.supabase_jwt_secret:
            raise AppError(401, "Invalid session", "invalid_token")
        key = s.supabase_jwt_secret
    elif alg in ("ES256", "RS256"):
        try:
            key = _jwks().get_signing_key_from_jwt(token).key
        except jwt.PyJWTError:
            raise AppError(401, "Invalid session", "invalid_token")
    else:
        raise AppError(401, "Invalid session", "invalid_token")   # rejects alg=none and anything unexpected
    try:
        return jwt.decode(
            token, key, algorithms=[alg], audience="authenticated", issuer=s.supabase_issuer,
            options={"require": ["exp", "sub", "iat"]}, leeway=10,
        )
    except jwt.ExpiredSignatureError:
        raise AppError(401, "Your session has expired", "token_expired")
    except jwt.PyJWTError:
        raise AppError(401, "Invalid session", "invalid_token")


async def load_user(user_id: UUID) -> CurrentUser | None:
    async with system_tx() as conn:
        row = await conn.fetchrow(
            """SELECT u.user_id, u.email, u.role, u.is_active,
                      a.applicant_id, r.recruiter_id, r.company_id, coalesce(r.is_company_admin, FALSE) AS is_company_admin
               FROM users u
               LEFT JOIN applicants a ON a.user_id = u.user_id
               LEFT JOIN recruiters r ON r.user_id = u.user_id
               WHERE u.user_id = $1""",
            user_id,
        )
    if row is None:
        return None
    if not row["is_active"]:
        raise AppError(403, "This account has been disabled", "account_disabled")
    return CurrentUser(row["user_id"], row["email"], row["role"], row["applicant_id"],
                       row["recruiter_id"], row["company_id"], row["is_company_admin"])


async def get_current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise AppError(401, "Sign in to continue", "not_authenticated")
    claims = await asyncio.to_thread(verify_token, creds.credentials)
    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError):
        raise AppError(401, "Invalid session", "invalid_token")
    user = await load_user(user_id)
    if user is None:
        raise AppError(403, "Your account is not set up yet. Try signing in again.", "account_missing")
    request.state.user_id = str(user.user_id)    # lets the rate limiter key by user
    return user


# --- Role guards --------------------------------------------------------------------------------

async def applicant_role(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "APPLICANT":
        raise AppError(403, "This area is for applicants", "wrong_role")
    return user


async def require_applicant(user: CurrentUser = Depends(applicant_role)) -> CurrentUser:
    if user.applicant_id is None:
        raise AppError(409, "Create your profile first", "onboarding_required")
    return user


async def recruiter_role(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "RECRUITER":
        raise AppError(403, "This area is for recruiters", "wrong_role")
    return user


async def require_recruiter(user: CurrentUser = Depends(recruiter_role)) -> CurrentUser:
    if user.recruiter_id is None:
        raise AppError(409, "Create or join a company first", "onboarding_required")
    return user


async def require_company_admin(user: CurrentUser = Depends(require_recruiter)) -> CurrentUser:
    if not user.is_company_admin:
        raise AppError(403, "Only company admins can do that", "not_admin")
    return user
