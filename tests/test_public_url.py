"""URL publique configurable (PUBLIC_URL)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.conftest import csrf, login
from tests.test_jellyfin import _configure

from mediatracker.api.app import create_app
from mediatracker.api.cookies import resolve_cookie_secure
from mediatracker.config import Settings
from mediatracker.security.sessions import SESSION_COOKIE

_PUBLIC_HTTPS = "https://mediatracker.example.com"
_LAN_IP = "192.0.2.1"


def _session_set_cookie(response) -> str:
    for key, value in response.headers.multi_items():
        if key.lower() == "set-cookie" and value.startswith(f"{SESSION_COOKIE}="):
            return value
    raise AssertionError("Cookie de session absent")


def _has_secure_attribute(set_cookie_line: str) -> bool:
    parts = [p.strip() for p in set_cookie_line.split(";")]
    return any(p.lower() == "secure" for p in parts[1:])


def _app_settings(tmp_path, **overrides) -> Settings:
    base = {
        "secret_key": "test-secret-key-0123456789",
        "admin_username": "admin",
        "admin_password": "Choix-Solide-42!",
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "data_dir": tmp_path / "data",
        "config_dir": tmp_path / "config",
        "disk_min_free_gb": 0,
        "alldebrid_api_key": None,
        "cookie_secure": "auto",
        "public_url": _PUBLIC_HTTPS,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def app_public_https(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLDEBRID_API_KEY", raising=False)
    application = create_app(_app_settings(tmp_path))
    yield application
    application.state.engine.dispose()


def test_public_url_absent_settings_default_none():
    settings = Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="Choix-Solide-42!",
    )
    assert settings.public_url is None


def test_public_url_invalid_rejected_at_startup():
    with pytest.raises(ValidationError):
        Settings(
            secret_key="test-secret-key-0123456789",
            public_url="ftp://example.com",
        )
    with pytest.raises(ValidationError):
        Settings(
            secret_key="test-secret-key-0123456789",
            public_url="https://mediatracker.example.com/app",
        )


def test_public_url_normalizes_trailing_slash():
    settings = Settings(
        secret_key="test-secret-key-0123456789",
        public_url="https://mediatracker.example.com/",
    )
    assert settings.public_url == "https://mediatracker.example.com"


def test_public_base_url_env_alias_still_works(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", f"http://{_LAN_IP}:8080")
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    settings = Settings(secret_key="test-secret-key-0123456789")
    assert settings.public_url == f"http://{_LAN_IP}:8080"


def test_resolve_cookie_secure_auto_lan_http_not_secure():
    assert not resolve_cookie_secure(
        "auto",
        "http",
        public_url=_PUBLIC_HTTPS,
        hostname=_LAN_IP,
        port=8080,
    )


def test_resolve_cookie_secure_auto_public_host_http_secure():
    assert resolve_cookie_secure(
        "auto",
        "http",
        public_url=_PUBLIC_HTTPS,
        hostname="mediatracker.example.com",
    )


def test_cookie_secure_lan_http_no_secure_with_public_url_https(app_public_https):
    with TestClient(app_public_https, base_url=f"http://{_LAN_IP}:8080") as client:
        response = login(client)
        assert response.status_code == 200, response.text
        assert not _has_secure_attribute(_session_set_cookie(response))


def test_cookie_secure_public_host_https_sets_secure(app_public_https):
    with TestClient(app_public_https, base_url="https://mediatracker.example.com") as client:
        response = login(client)
        assert response.status_code == 200, response.text
        assert _has_secure_attribute(_session_set_cookie(response))


def test_arbitrary_host_accepted_with_public_url(app_public_https):
    with TestClient(app_public_https, base_url="http://nas.local:8080") as client:
        assert client.get("/healthz").status_code == 200
        assert login(client).status_code == 200


def test_jellyfin_webhook_url_uses_public_url(app_public_https):
    with TestClient(app_public_https, base_url="https://mediatracker.example.com") as client:
        login(client)
        _configure(client)
        status = client.get("/api/v1/integrations/jellyfin", headers=csrf(client))
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["webhook_url"] == f"{_PUBLIC_HTTPS}/api/v1/integrations/jellyfin/webhook"
