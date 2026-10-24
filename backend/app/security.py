"""HTTP hardening: request ids, security headers, request body limits and rate limiting.

Written as plain ASGI middleware so the body limit is enforced while the request streams in,
not after an oversized body has already been buffered into memory.
"""
import secrets

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import get_settings


# --- Rate limiting --------------------------------------------------------------------------------
# In-memory storage: correct for a single API instance. To run several instances, point
# `storage_uri` at Redis; nothing else changes.
def rate_limit_key(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)   # set by the auth dependency
    return f"user:{user_id}" if user_id else f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=rate_limit_key, default_limits=[get_settings().rate_limit_default], headers_enabled=False)


# --- Security headers + request id --------------------------------------------------------------------
class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        s = get_settings()
        self.headers = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cache-control", b"no-store"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
            (b"cross-origin-opener-policy", b"same-origin"),
        ]
        if s.is_production:
            self.headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(8)
        scope.setdefault("state", {})["request_id"] = request_id
        is_docs = scope["path"].startswith(("/docs", "/openapi.json", "/redoc"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in self.headers if k not in existing]
                if not is_docs:
                    headers.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# --- Request body size limit ------------------------------------------------------------------------
class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Rejects bodies over the limit (5 MB-ish for resume uploads, 256 KB for everything else)."""

    def __init__(self, app: ASGIApp, upload_paths: tuple[str, ...]):
        self.app = app
        s = get_settings()
        self.default_limit = s.max_json_body_bytes
        self.upload_limit = s.max_resume_bytes + 64 * 1024   # multipart overhead
        self.upload_paths = upload_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = self.upload_limit if scope["path"].startswith(self.upload_paths) else self.default_limit

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        await _reject(send, limit)
                        return
                except ValueError:
                    await _reject(send, limit, status=400)
                    return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await _reject(send, limit)


async def _reject(send: Send, limit: int, status: int = 413) -> None:
    text = (b'{"error":{"code":"payload_too_large","message":"Request is too large (limit %d KB)"}}' % (limit // 1024)
            if status == 413 else b'{"error":{"code":"bad_request","message":"Malformed request"}}')
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(text)).encode())]})
    await send({"type": "http.response.body", "body": text})
