"""Error handling: one JSON shape for every error, and no internals leaked to clients.

Database rule violations raised by our own triggers use SQLSTATE 'SL<http status>' and carry a
message written for end users, so those messages are passed through. Every other database or
server error is logged with a request id and returned as a generic message.
"""
import logging

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

log = logging.getLogger("shortlist")


class AppError(Exception):
    def __init__(self, status: int, message: str, code: str = "error"):
        self.status, self.message, self.code = status, message, code


def _body(code: str, message: str, request: Request, **extra) -> dict:
    return {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}}


_PG_CONSTRAINT_MESSAGES = {
    "applications_job_id_applicant_id_key": "You have already applied to this job",
    "no_interviewer_double_booking": "The interviewer already has an interview at that time",
    "no_candidate_double_booking": "The candidate already has an interview at that time",
    "uq_offers_one_live": "This candidate already has an active offer",
    "uq_skills_name_ci": "That skill already exists",
    "candidate_skills_pkey": "That skill is already on your profile",
    "job_skills_pkey": "That skill is listed twice",
    "applicants_user_id_key": "Your profile already exists",
    "recruiters_user_id_key": "You already belong to a company",
}


def map_db_error(exc: asyncpg.PostgresError) -> tuple[int, str, str]:
    state = getattr(exc, "sqlstate", "") or ""
    if state.startswith("SL") and state[2:].isdigit():
        return int(state[2:]), "rule_violation", exc.args[0] if exc.args else "Request not allowed"
    constraint = getattr(exc, "constraint_name", None)
    if isinstance(exc, (asyncpg.UniqueViolationError, asyncpg.ExclusionViolationError)):
        return 409, "conflict", _PG_CONSTRAINT_MESSAGES.get(constraint, "This conflicts with existing data")
    if isinstance(exc, asyncpg.CheckViolationError):
        return 422, "invalid", "One of the values is out of the allowed range"
    if isinstance(exc, asyncpg.ForeignKeyViolationError):
        return 404, "not_found", "A referenced record does not exist"
    if isinstance(exc, asyncpg.NotNullViolationError):
        return 422, "invalid", "A required value is missing"
    if isinstance(exc, (asyncpg.InsufficientPrivilegeError,)):
        return 403, "forbidden", "You don't have permission to do that"
    if isinstance(exc, asyncpg.StringDataRightTruncationError):
        return 422, "invalid", "One of the values is too long"
    if isinstance(exc, (asyncpg.InvalidTextRepresentationError, asyncpg.DataError)):
        return 422, "invalid", "One of the values has the wrong format"
    return 500, "server_error", "Something went wrong on our side"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, request), status_code=exc.status)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(_body("http_error", message, request), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = []
        for e in exc.errors()[:10]:
            loc = ".".join(str(p) for p in e.get("loc", []) if p not in ("body", "query", "path"))
            fields.append({"field": loc, "message": e.get("msg", "invalid")})
        return JSONResponse(_body("validation_error", "Some fields are invalid", request, fields=fields), status_code=422)

    @app.exception_handler(ValidationError)
    async def _model_validation(request: Request, exc: ValidationError):
        fields = [{"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg", "invalid")} for e in exc.errors()[:10]]
        return JSONResponse(_body("validation_error", "Some fields are invalid", request, fields=fields), status_code=422)

    @app.exception_handler(asyncpg.PostgresError)
    async def _db_error(request: Request, exc: asyncpg.PostgresError):
        status, code, message = map_db_error(exc)
        if status >= 500:
            log.exception("database error [%s]", getattr(request.state, "request_id", "-"), exc_info=exc)
        return JSONResponse(_body(code, message, request), status_code=status)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error [%s]", getattr(request.state, "request_id", "-"), exc_info=exc)
        return JSONResponse(_body("server_error", "Something went wrong on our side", request), status_code=500)
