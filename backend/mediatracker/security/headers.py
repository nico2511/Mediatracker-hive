"""HTTP response hardening headers for API and static SPA."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Vue build: inline critical background style in index.html; assets and API stay same-origin.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "frame-src 'self' https://www.youtube-nocookie.com; "
    "frame-ancestors 'none'"
)

# Swagger UI / ReDoc chargent JS et CSS depuis un CDN public.
_CSP_EXEMPT_PATHS = frozenset({"/api/docs", "/api/redoc"})


def apply_security_headers(response: Response, *, set_csp: bool = True) -> None:
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if set_csp:
        response.headers.setdefault("Content-Security-Policy", _CONTENT_SECURITY_POLICY)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        apply_security_headers(response, set_csp=request.url.path not in _CSP_EXEMPT_PATHS)
        return response
