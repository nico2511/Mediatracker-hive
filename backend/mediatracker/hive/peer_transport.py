"""Transport HTTP vers une instance pair (tests : ASGI en mémoire)."""

from __future__ import annotations

from typing import Any

import httpx

from mediatracker.hive.peer_outbound import enforce_hive_peer_request_url
from mediatracker.security.ssrf import httpx_request_event_hooks

_test_asgi_app: Any = None
_test_asgi_apps: dict[str, Any] | None = None


def set_peer_asgi_app_for_tests(app: Any | None) -> None:
    """Réservé aux tests : achemine les URL Hive vers une autre app sans réseau."""
    global _test_asgi_app, _test_asgi_apps
    _test_asgi_app = app
    _test_asgi_apps = None


def set_peer_asgi_apps_for_tests(apps: dict[str, Any] | None) -> None:
    """Mappe une base_url vers une app ASGI (tests multi-instances)."""
    global _test_asgi_app, _test_asgi_apps
    _test_asgi_apps = {k.rstrip("/"): v for k, v in apps.items()} if apps else None
    _test_asgi_app = None


def _resolve_test_app(base_url: str) -> Any | None:
    if _test_asgi_apps is not None:
        hit = _test_asgi_apps.get(base_url.rstrip("/"))
        if hit is not None:
            return hit
        if len(_test_asgi_apps) == 1:
            return next(iter(_test_asgi_apps.values()))
    return _test_asgi_app


def peer_request(
    *,
    base_url: str,
    method: str,
    url_path: str,
    headers: dict[str, str],
    body: bytes,
) -> httpx.Response:
    resolved = _resolve_test_app(base_url)
    if resolved is not None:
        from starlette.testclient import TestClient

        client = TestClient(resolved, base_url=base_url.rstrip("/"))
        raw = client.request(method.upper(), url_path, content=body, headers=headers)
        return httpx.Response(
            status_code=raw.status_code,
            headers=dict(raw.headers),
            content=raw.content,
            request=httpx.Request(method, base_url + url_path),
        )
    enforce_hive_peer_request_url(base_url, url_path)
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        follow_redirects=True,
        event_hooks=httpx_request_event_hooks(),
    ) as client:
        return client.request(method.upper(), url_path, content=body, headers=headers)
