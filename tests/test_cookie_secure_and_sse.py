"""Cookies Secure (COOKIE_SECURE) et en-têtes du flux SSE."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.conftest import login
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.security.sessions import SESSION_COOKIE


def _session_set_cookie(response) -> str:
    for key, value in response.headers.multi_items():
        if key.lower() == "set-cookie" and value.startswith(f"{SESSION_COOKIE}="):
            return value
    raise AssertionError("Cookie de session absent")


def _has_secure_attribute(set_cookie_line: str) -> bool:
    parts = [p.strip() for p in set_cookie_line.split(";")]
    return any(p.lower() == "secure" for p in parts[1:])


@pytest.fixture
def app_auto(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLDEBRID_API_KEY", raising=False)
    settings = Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="Choix-Solide-42!",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
        cookie_secure="auto",
        forwarded_allow_ips="127.0.0.1",
    )
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


def test_cookie_secure_auto_http_no_secure_flag(app_auto):
    with TestClient(app_auto, base_url="http://testserver") as client:
        response = login(client)
        line = _session_set_cookie(response)
        assert not _has_secure_attribute(line)


def test_cookie_secure_auto_https_sets_secure_flag(app_auto):
    with TestClient(app_auto, base_url="https://testserver") as client:
        response = login(client)
        line = _session_set_cookie(response)
        assert _has_secure_attribute(line)


def test_cookie_secure_auto_forwarded_proto_https(app_auto):
    # TestClient se connecte avec l'hôte ASGI « testclient » ; en prod le client direct est 127.0.0.1.
    wrapped = ProxyHeadersMiddleware(app_auto, trusted_hosts="127.0.0.1,testclient")
    with TestClient(wrapped, base_url="http://testserver") as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Choix-Solide-42!"},
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"},
        )
        assert response.status_code == 200, response.text
        line = _session_set_cookie(response)
        assert _has_secure_attribute(line)


@pytest.fixture
def app_cookie_true(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLDEBRID_API_KEY", raising=False)
    settings = Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="Choix-Solide-42!",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
        cookie_secure=True,
    )
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def app_cookie_false(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLDEBRID_API_KEY", raising=False)
    settings = Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="Choix-Solide-42!",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
        cookie_secure=False,
    )
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


def test_cookie_secure_true_always_secure(app_cookie_true):
    with TestClient(app_cookie_true, base_url="http://testserver") as client:
        response = login(client)
        assert _has_secure_attribute(_session_set_cookie(response))


def test_cookie_secure_false_never_secure(app_cookie_false):
    with TestClient(app_cookie_false, base_url="https://testserver") as client:
        response = login(client)
        assert not _has_secure_attribute(_session_set_cookie(response))


def test_sse_stream_proxy_headers(client, app):
    login(client)
    app.state.sse_limit = 1
    stream = client.get("/api/v1/events/stream")
    assert stream.status_code == 200
    assert stream.headers.get("cache-control") == "no-cache"
    assert stream.headers.get("x-accel-buffering") == "no"
