"""AllDebrid PIN account API (mocked HTTP)."""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.db.models import Provider, Setting
from mediatracker.db.session import session_scope
from mediatracker.integrations.alldebrid_account import (
    PIN_SETTING_KEY,
    response_has_secret_leak,
)
from mediatracker.providers.debrid.alldebrid import AllDebridClient, AllDebridError
from mediatracker.security.crypto import decrypt_secret, encrypt_secret, is_encrypted

FAKE_KEY = "fictive-alldebrid-key-abc"
PIN_SESSION = {
    "pin": "WXYZ",
    "check": "check-token-fictif",
    "user_url": "https://alldebrid.com/pin/?pin=WXYZ",
    "base_url": "https://alldebrid.com/pin/",
    "expires_in": 600,
}


class PinClientMock:
    calls: list[str] = []
    next_check = "pending"

    def __init__(self, *args, **kwargs):
        del args, kwargs

    def close(self):
        pass

    def pin_get(self):
        PinClientMock.calls.append("get")
        return {"status": "success", "data": dict(PIN_SESSION)}

    def pin_check(self, pin: str, check: str):
        PinClientMock.calls.append(f"check:{pin}:{check}")
        if PinClientMock.next_check == "expired":
            raise AllDebridError("PIN_EXPIRED", "Code expiré.", retryable=False)
        if PinClientMock.next_check == "pending":
            return {"status": "success", "data": {"activated": False, "expires_in": 120}}
        return {
            "status": "success",
            "data": {"activated": True, "expires_in": 90, "apikey": FAKE_KEY},
        }

    def get_user(self):
        return {"username": "utilisateur-fictif", "isPremium": True}


def _admin_headers(client):
    login(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": "alldebrid", "scopes": ["admin"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def _token_headers(client, scopes: list[str]):
    login(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": f"{'-'.join(scopes)}-alldebrid", "scopes": scopes},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {created.json()['token']}"}


def _provider_stored(app):
    factory = app.state.session_factory
    settings = app.state.settings
    with session_scope(factory) as session:
        row = session.scalar(select(Provider).where(Provider.type == "alldebrid"))
        assert row is not None
        stored = (row.config or {}).get("api_key", "")
        return stored, settings


def _pin_setting_exists(app) -> bool:
    factory = app.state.session_factory
    with session_scope(factory) as session:
        return session.get(Setting, PIN_SETTING_KEY) is not None


def test_mutations_require_admin(client):
    read = _token_headers(client, ["read"])
    write = _token_headers(client, ["write"])
    for headers in (read, write):
        for path in (
            "/api/v1/integrations/alldebrid/account/pin/start",
            "/api/v1/integrations/alldebrid/account/pin/poll",
            "/api/v1/integrations/alldebrid/account/pin/cancel",
            "/api/v1/integrations/alldebrid/account/disconnect",
            "/api/v1/integrations/alldebrid/account/manual",
        ):
            payload = {"api_key": FAKE_KEY} if path.endswith("/manual") else None
            response = client.post(path, headers=headers, json=payload)
            assert response.status_code == 403, path


def test_pin_flow_ok_encrypts_and_never_leaks(client, app, monkeypatch):
    PinClientMock.calls = []
    PinClientMock.next_check = "pending"
    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: PinClientMock())
    headers = _admin_headers(client)
    start = client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)
    assert start.status_code == 200
    assert start.json()["pin_phase"] == "pending"
    assert not response_has_secret_leak(start.json(), forbidden=FAKE_KEY)

    PinClientMock.next_check = "activated"
    done = client.post("/api/v1/integrations/alldebrid/account/pin/poll", headers=headers)
    assert done.status_code == 200
    final = done.json()
    assert final["connected"] is True
    assert final["source"] == "db"
    assert not response_has_secret_leak(final, forbidden=FAKE_KEY)
    assert json.dumps(final).count(FAKE_KEY) == 0

    stored, settings = _provider_stored(app)
    assert is_encrypted(stored)
    assert decrypt_secret(stored, settings.secret_key) == FAKE_KEY


def test_pin_expired_and_invalid_codes(client, app, monkeypatch):
    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: PinClientMock())
    headers = _admin_headers(client)
    client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)

    PinClientMock.next_check = "expired"
    poll = client.post("/api/v1/integrations/alldebrid/account/pin/poll", headers=headers)
    assert poll.json()["pin_phase"] == "expired"

    client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)
    PinClientMock.next_check = "invalid"

    def fake_check(pin, check):
        raise AllDebridError("PIN_INVALID", "PIN invalide", retryable=False)

    monkeypatch.setattr(PinClientMock, "pin_check", lambda self, pin, check: fake_check(pin, check))
    poll = client.post("/api/v1/integrations/alldebrid/account/pin/poll", headers=headers)
    assert poll.json()["pin_phase"] == "expired"


def test_pin_cancel_deletes_session(client, app, monkeypatch):
    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: PinClientMock())
    headers = _admin_headers(client)
    client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)
    assert _pin_setting_exists(app)
    cancel = client.post("/api/v1/integrations/alldebrid/account/pin/cancel", headers=headers)
    assert cancel.status_code == 200
    assert not _pin_setting_exists(app)


def test_local_expiration_skips_network(client, app, monkeypatch):
    PinClientMock.calls = []

    def boom():
        raise AssertionError("pin_check ne doit pas être appelé")

    class LocalClient(PinClientMock):
        def pin_check(self, pin, check):
            return boom()

    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: LocalClient())
    headers = _admin_headers(client)
    client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)
    factory = app.state.session_factory
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with session_scope(factory) as session:
        settings = app.state.settings
        row = session.get(Setting, PIN_SETTING_KEY)
        payload = json.loads(decrypt_secret(row.value, settings.secret_key))
        payload["expires_at"] = expired_at
        row.value = encrypt_secret(json.dumps(payload), settings.secret_key)
        session.commit()

    poll = client.post("/api/v1/integrations/alldebrid/account/pin/poll", headers=headers)
    assert poll.json()["pin_phase"] == "expired"
    assert PinClientMock.calls == ["get"]
    assert not _pin_setting_exists(app)


def test_pin_expires_at_uses_remaining_seconds(client, app, monkeypatch):
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    monkeypatch.setattr(
        "mediatracker.integrations.alldebrid_account._utcnow",
        lambda: clock["now"],
    )
    PinClientMock.calls = []
    PinClientMock.next_check = "pending"
    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: PinClientMock())
    headers = _admin_headers(client)
    client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)
    assert PinClientMock.calls == ["get"]

    clock["now"] = t0 + timedelta(seconds=300)
    mid = client.post("/api/v1/integrations/alldebrid/account/pin/poll", headers=headers)
    assert mid.status_code == 200
    assert mid.json()["pin_phase"] == "pending"
    assert any(call.startswith("check:") for call in PinClientMock.calls)

    clock["now"] = t0 + timedelta(seconds=601)

    def boom():
        raise AssertionError("pin_check ne doit pas être appelé après échéance")

    class LateClient(PinClientMock):
        def pin_check(self, pin, check):
            return boom()

    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: LateClient())
    late = client.post("/api/v1/integrations/alldebrid/account/pin/poll", headers=headers)
    assert late.json()["pin_phase"] == "expired"
    assert PinClientMock.calls.count("get") == 1


def test_account_get_clears_expired_session(client, app, monkeypatch):
    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    monkeypatch.setattr(
        "mediatracker.integrations.alldebrid_account._utcnow",
        lambda: clock["now"],
    )
    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: PinClientMock())
    headers = _admin_headers(client)
    client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers)
    assert _pin_setting_exists(app)
    clock["now"] = t0 + timedelta(seconds=900)
    read = client.get("/api/v1/integrations/alldebrid/account", headers=headers)
    assert read.json()["pin_phase"] == "expired"
    assert not _pin_setting_exists(app)


def test_env_key_priority_and_disabled_provider(client, app, monkeypatch):
    monkeypatch.setenv("ALLDEBRID_API_KEY", "env-fictive-key")
    app.state.settings = app.state.settings.model_copy(update={"alldebrid_api_key": "env-fictive-key"})
    factory = app.state.session_factory
    with session_scope(factory) as session:
        row = session.scalar(select(Provider).where(Provider.type == "alldebrid"))
        row.enabled = False
        session.commit()

    from mediatracker.core.pipeline.clients import _debrid

    with session_scope(factory) as session:
        _row, client_debrid = _debrid(session, app.state.settings, None)
        assert client_debrid is not None

    login(client)
    read = client.get("/api/v1/integrations/alldebrid/account")
    assert read.json()["source"] == "env"
    headers = _admin_headers(client)
    for path in (
        "/api/v1/integrations/alldebrid/account/pin/start",
        "/api/v1/integrations/alldebrid/account/pin/cancel",
        "/api/v1/integrations/alldebrid/account/manual",
        "/api/v1/integrations/alldebrid/account/disconnect",
    ):
        body = {"api_key": "x"} if path.endswith("/manual") else None
        assert client.post(path, headers=headers, json=body).status_code == 409
    monkeypatch.delenv("ALLDEBRID_API_KEY", raising=False)
    app.state.settings = app.state.settings.model_copy(update={"alldebrid_api_key": None})


def test_secret_key_rotation_graceful(client, app, monkeypatch):
    monkeypatch.setattr(AllDebridClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(AllDebridClient, "get_user", lambda self: {"isPremium": True})
    monkeypatch.setattr(AllDebridClient, "close", lambda self: None)
    PinClientMock.calls = []
    monkeypatch.setattr("mediatracker.integrations.alldebrid_account._pin_client", lambda: PinClientMock())

    headers = _admin_headers(client)
    client.post(
        "/api/v1/integrations/alldebrid/account/manual",
        json={"api_key": FAKE_KEY},
        headers=headers,
    )
    app.state.settings = app.state.settings.model_copy(update={"secret_key": "another-test-secret-key-987654321"})
    get = client.get("/api/v1/integrations/alldebrid/account", headers=headers)
    assert get.status_code == 200
    body = get.json()
    assert body["connected"] is False
    assert body["source"] == "none"
    assert body["account_notice"]
    assert client.post("/api/v1/integrations/alldebrid/account/pin/start", headers=headers).status_code == 200
    assert (
        client.post(
            "/api/v1/integrations/alldebrid/account/manual",
            json={"api_key": FAKE_KEY},
            headers=headers,
        ).status_code
        == 200
    )


def test_manual_key_no_log_leak(client, app, monkeypatch, caplog):
    monkeypatch.setattr(AllDebridClient, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(AllDebridClient, "get_user", lambda self: {"isPremium": True})
    monkeypatch.setattr(AllDebridClient, "close", lambda self: None)
    headers = _admin_headers(client)
    caplog.set_level(logging.INFO)
    assert (
        client.post(
            "/api/v1/integrations/alldebrid/account/manual",
            json={"api_key": FAKE_KEY},
            headers=headers,
        ).status_code
        == 200
    )
    dump = json.dumps(client.get("/api/v1/integrations/alldebrid/account", headers=headers).json())
    assert FAKE_KEY not in dump
    assert FAKE_KEY not in caplog.text
