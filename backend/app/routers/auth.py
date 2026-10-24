"""Sign-up / sign-in for the two portals, proxied to Supabase Auth.

Going through the API (rather than the browser calling Supabase directly) means:
  * our own rate limits apply to every credential attempt
  * the portal check happens before any token reaches the browser: a recruiter who signs in on the
    applicant page is refused, and the session Supabase just created is revoked
  * the role is chosen by which portal you sign up on, never by a field the client controls
  * the frontend never needs a Supabase key at all
"""
import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from ..auth import CurrentUser, _bearer, get_current_user, load_user
from ..config import get_settings
from ..db import system_tx
from ..errors import AppError
from ..schemas import ForgotPasswordIn, LoginIn, RefreshIn, ResetPasswordIn, SignupIn
from ..security import limiter

router = APIRouter(prefix="/api", tags=["auth"])
log = logging.getLogger("shortlist.auth")
_http: httpx.AsyncClient | None = None
S = get_settings()


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False)
    return _http


async def _gotrue(method: str, path: str, *, json: dict | None = None, token: str | None = None,
                  params: dict | None = None) -> httpx.Response:
    headers = {"apikey": S.supabase_anon_key, "Authorization": f"Bearer {token or S.supabase_anon_key}"}
    try:
        return await _client().request(method, f"{S.supabase_issuer}{path}", json=json, headers=headers, params=params)
    except httpx.HTTPError:
        log.warning("supabase auth unreachable")
        raise AppError(503, "Sign-in is temporarily unavailable. Please try again shortly.", "auth_unavailable")


def _error_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        return str(body.get("error_code") or body.get("code") or body.get("error") or "")
    except ValueError:
        return ""


def _session(data: dict, user: CurrentUser) -> dict:
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data.get("expires_in", 3600),
        "user": {"email": user.email, "role": user.role,
                 "onboarded": bool(user.applicant_id or user.recruiter_id)},
    }


@router.post("/auth/signup")
@limiter.limit(S.rate_limit_auth)
async def signup(request: Request, body: SignupIn):
    role = "RECRUITER" if body.portal == "recruiter" else "APPLICANT"
    resp = await _gotrue("POST", "/signup", params={"redirect_to": f"{S.frontend_origin}/{body.portal}/login.html?confirmed=1"},
                         json={"email": body.email, "password": body.password, "data": {"role": role}})
    code = _error_code(resp)
    if resp.status_code == 429:
        raise AppError(429, "Too many attempts. Please wait a minute.", "rate_limited")
    if code == "weak_password":
        raise AppError(422, "Choose a stronger password", "weak_password")
    if resp.status_code >= 400 and code not in ("user_already_exists", "email_exists"):
        raise AppError(400, "We couldn't create that account. Check the email address and try again.", "signup_failed")
    # Same answer whether or not the address was already registered, so sign-up can't be used to
    # discover which emails have accounts.
    needs_confirmation = resp.status_code >= 400 or not resp.json().get("access_token")
    return {"needs_confirmation": needs_confirmation,
            "message": "Check your inbox to confirm your email, then sign in." if needs_confirmation else "Account created."}


@router.post("/auth/login")
@limiter.limit(S.rate_limit_auth)
async def login(request: Request, body: LoginIn):
    resp = await _gotrue("POST", "/token", params={"grant_type": "password"},
                         json={"email": body.email, "password": body.password})
    if resp.status_code == 429:
        raise AppError(429, "Too many attempts. Please wait a minute.", "rate_limited")
    if resp.status_code >= 400:
        if _error_code(resp) == "email_not_confirmed":
            raise AppError(403, "Confirm your email address first. Check your inbox for the link.", "email_not_confirmed")
        raise AppError(401, "Incorrect email or password", "invalid_credentials")

    data = resp.json()
    user = await load_user(UUID(data["user"]["id"]))
    if user is None:
        raise AppError(403, "Your account isn't fully set up. Contact support.", "account_missing")

    expected = body.portal.upper()
    if user.role != expected:
        await _gotrue("POST", "/logout", token=data["access_token"])   # don't leave a live session behind
        if user.role == "ADMIN":
            raise AppError(403, "Admin accounts can't sign in to this portal.", "wrong_portal")
        other = "recruiter" if user.role == "RECRUITER" else "applicant"
        raise AppError(403, f"This account is registered as {'a' if other == 'recruiter' else 'an'} {other}. "
                            f"Sign in from the {other} portal.", "wrong_portal")

    async with system_tx() as conn:
        await conn.execute("INSERT INTO audit_logs (user_id, action, entity_type, ip_address) VALUES ($1, 'LOGIN', 'user', $2::inet)",
                           user.user_id, request.client.host if request.client else None)
    return _session(data, user)


@router.post("/auth/refresh")
@limiter.limit("30/minute")
async def refresh(request: Request, body: RefreshIn):
    resp = await _gotrue("POST", "/token", params={"grant_type": "refresh_token"}, json={"refresh_token": body.refresh_token})
    if resp.status_code >= 400:
        raise AppError(401, "Your session has expired. Sign in again.", "session_expired")
    data = resp.json()
    user = await load_user(UUID(data["user"]["id"]))
    if user is None:
        raise AppError(401, "Your session has expired. Sign in again.", "session_expired")
    return _session(data, user)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    if creds and creds.credentials:
        await _gotrue("POST", "/logout", token=creds.credentials)   # revokes the refresh token server-side
    return Response(status_code=204)


@router.post("/auth/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request, body: ForgotPasswordIn):
    await _gotrue("POST", "/recover", params={"redirect_to": f"{S.frontend_origin}/reset-password.html?portal={body.portal}"},
                  json={"email": body.email})
    return {"message": "If an account exists for that email, a reset link is on its way."}


@router.post("/auth/reset-password")
@limiter.limit(S.rate_limit_auth)
async def reset_password(request: Request, body: ResetPasswordIn):
    resp = await _gotrue("PUT", "/user", token=body.access_token, json={"password": body.password})
    if resp.status_code >= 400:
        if _error_code(resp) == "weak_password":
            raise AppError(422, "Choose a stronger password", "weak_password")
        raise AppError(400, "This reset link is invalid or has expired. Request a new one.", "reset_failed")
    return {"message": "Password updated. You can sign in now."}


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    async with system_tx() as conn:
        profile = None
        if user.applicant_id:
            profile = await conn.fetchrow("SELECT first_name, last_name FROM applicants WHERE applicant_id = $1", user.applicant_id)
        elif user.recruiter_id:
            profile = await conn.fetchrow(
                """SELECT r.first_name, r.last_name, r.designation, r.is_company_admin, c.company_id, c.company_name
                   FROM recruiters r JOIN companies c ON c.company_id = r.company_id WHERE r.recruiter_id = $1""",
                user.recruiter_id)
    return {"email": user.email, "role": user.role, "onboarded": profile is not None,
            "profile": dict(profile) if profile else None}
