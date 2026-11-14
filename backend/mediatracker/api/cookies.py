"""Cookies de session et CSRF.

L'attribut ``Secure`` dépend de ``COOKIE_SECURE`` (``true``, ``false`` ou ``auto``).
En mode ``auto`` : Secure si la requête est en HTTPS (``X-Forwarded-Proto`` si proxy de
confiance), ou si ``PUBLIC_URL`` est en ``https://`` et que l'hôte de la requête est celui
de ``PUBLIC_URL``.
"""

from __future__ import annotations

import secrets
from typing import Literal

from starlette.requests import Request
from starlette.responses import Response

from mediatracker.core.public_url import public_url_scheme, request_host_matches_public_url
from mediatracker.security.sessions import CSRF_COOKIE, SESSION_COOKIE

CookieSecureMode = bool | Literal["auto"]


def resolve_cookie_secure(
    mode: CookieSecureMode,
    scheme: str,
    *,
    public_url: str | None = None,
    hostname: str | None = None,
    port: int | None = None,
) -> bool:
    """Retourne si les cookies doivent porter l'attribut Secure pour cette requête."""
    if mode is True:
        return True
    if mode is False:
        return False
    if scheme.lower() == "https":
        return True
    if (
        public_url
        and public_url_scheme(public_url) == "https"
        and request_host_matches_public_url(public_url, hostname, port, scheme)
    ):
        return True
    return False


def cookie_secure_for_request(request: Request) -> bool:
    settings = request.app.state.settings
    return resolve_cookie_secure(
        settings.cookie_secure,
        request.url.scheme,
        public_url=settings.public_url,
        hostname=request.url.hostname,
        port=request.url.port,
    )


def set_session_cookie(response: Response, value: str, max_age: int, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=max_age,
        httponly=True,
        secure=cookie_secure_for_request(request),
        samesite="lax",
        path="/",
    )


def set_csrf_cookie(response: Response, max_age: int, request: Request, value: str | None = None) -> str:
    token = value or secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=max_age,
        httponly=False,
        secure=cookie_secure_for_request(request),
        samesite="lax",
        path="/",
    )
    return token


def clear_auth_cookies(response: Response, request: Request) -> None:
    secure = cookie_secure_for_request(request)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax", secure=secure)
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax", secure=secure)
