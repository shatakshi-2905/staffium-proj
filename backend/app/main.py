"""FastAPI application entry point.

    uvicorn app.main:app --proxy-headers --forwarded-allow-ips="*"
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import db
from .config import get_settings
from .errors import install_error_handlers
from .routers import applicant, applications, auth, common, companies, jobs, recruiter, recruiter_ops, resumes, reviews
from .security import BodySizeLimitMiddleware, SecurityHeadersMiddleware, limiter
from .services import embeddings, worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("staffium")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await db.open_pool()
    await asyncio.to_thread(embeddings.warm_up)
    task = asyncio.create_task(worker.run_forever()) if s.worker_enabled else None
    log.info("API ready (environment=%s)", s.environment)
    yield
    if task:
        task.cancel()
    await db.close_pool()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Staffium API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if s.is_production else "/docs",      # no public API explorer in production
        redoc_url=None,
        openapi_url=None if s.is_production else "/openapi.json",
    )

    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(request: Request, exc: RateLimitExceeded):
        return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests. Please slow down and try again shortly.",
                                       "request_id": getattr(request.state, "request_id", None)}}, status_code=429)

    install_error_handlers(app)

    # Middleware runs outermost-last: security headers wrap everything, including CORS preflights
    # and 413/429 responses. CORS sits outside the body limit so a 413 still carries the CORS
    # header and the browser can show its message instead of a generic network error.
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, upload_paths=("/api/resumes",))
    app.add_middleware(CORSMiddleware, allow_origins=[s.frontend_origin], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"], max_age=600)
    app.add_middleware(SecurityHeadersMiddleware)

    for r in (auth, common, applicant, resumes, jobs, applications, companies, recruiter, recruiter_ops, reviews):
        app.include_router(r.router)

    @app.get("/api/health", include_in_schema=False)
    async def health():
        async with db.system_tx() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok"}

    return app


app = create_app()
