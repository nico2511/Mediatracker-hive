"""Unit tests for Watchtower update-now client logic."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import func, select
from tests.conftest import csrf, login

from mediatracker.core.watchtower import request_update_now, watchtower_configured
from mediatracker.db.models import Event, Setting
from mediatracker.security.crypto import encrypt_secret

_SECRET = "wt-test-token-9f3a2b1c"
_IMAGE = "ghcr.io/nico2511/mediatracker-hive"


@pytest.fixture
def configured_db(client, app):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    now = datetime.now(timezone.utc)

    def _set(key: str, value: str, *, secret: bool) -> None:
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value, updated_at=now, is_secret=secret))
        else:
            row.value = value
            row.is_secret = secret
            row.updated_at = now

    _set("watchtower_url", "http://watchtower.test:8080", secret=False)
    _set("watchtower_token", encrypt_secret(_SECRET, settings.secret_key), secret=True)
    db.commit()
    db.close()


def test_watchtower_configured_requires_url_and_token(app, configured_db):
    del configured_db
    db = app.state.session_factory()
    try:
        assert watchtower_configured(db, app.state.settings)
        row = db.get(Setting, "watchtower_url")
        assert row is not None
        row.value = ""
        db.commit()
        assert not watchtower_configured(db, app.state.settings)
    finally:
        db.close()


def test_request_sends_bearer_and_image(app, configured_db):
    del configured_db
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers.get("Authorization") == f"Bearer {_SECRET}"
        assert request.url.params.get("image") == _IMAGE
        return httpx.Response(200, request=request)

    db = app.state.session_factory()
    try:
        result = request_update_now(db, app.state.settings, transport=httpx.MockTransport(handler))
        assert result.status == 200
        assert result.code == "already_up_to_date"
        assert "déjà à jour" in result.detail.casefold()
        assert len(calls) == 1
    finally:
        db.close()


def test_request_maps_401(app, configured_db):
    del configured_db

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    db = app.state.session_factory()
    try:
        result = request_update_now(db, app.state.settings, transport=httpx.MockTransport(handler))
        assert result.status == 502
        assert result.code == "token_refused"
    finally:
        db.close()


def test_request_connect_error(app, configured_db):
    del configured_db

    def handler(request: httpx.Request):
        raise httpx.ConnectError("refused")

    db = app.state.session_factory()
    try:
        result = request_update_now(db, app.state.settings, transport=httpx.MockTransport(handler))
        assert result.status == 502
        assert result.code == "unreachable"
    finally:
        db.close()


def test_request_read_timeout(app, configured_db):
    del configured_db

    def handler(request: httpx.Request):
        raise httpx.ReadTimeout("slow")

    db = app.state.session_factory()
    try:
        result = request_update_now(db, app.state.settings, transport=httpx.MockTransport(handler))
        assert result.status == 202
        assert result.code == "sent"
    finally:
        db.close()


def test_request_other_status(app, configured_db):
    del configured_db

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    db = app.state.session_factory()
    try:
        result = request_update_now(db, app.state.settings, transport=httpx.MockTransport(handler))
        assert result.status == 502
        assert "erreur" in result.detail.casefold()
    finally:
        db.close()


def test_token_not_in_responses_or_logs(app, client, configured_db, caplog):
    del client

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    db = app.state.session_factory()
    try:
        with caplog.at_level("WARNING"):
            result = request_update_now(db, app.state.settings, transport=httpx.MockTransport(handler))
        assert result.status == 502
        assert _SECRET not in caplog.text
        assert _SECRET not in result.detail
    finally:
        db.close()


def test_update_requested_logged_for_each_call(app, configured_db):
    del configured_db

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    db = app.state.session_factory()
    try:
        transport = httpx.MockTransport(handler)
        request_update_now(db, app.state.settings, transport=transport)
        request_update_now(db, app.state.settings, transport=transport)
        db.commit()
        count = db.scalar(select(func.count()).select_from(Event).where(Event.type == "update.requested"))
        assert count == 2
    finally:
        db.close()


def _bearer(client, scopes: list[str]) -> dict[str, str]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": "wt-scope", "scopes": scopes},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def test_update_now_requires_write_scope(client, configured_db):
    del configured_db
    login(client)
    headers = _bearer(client, ["read"])
    response = client.post("/api/v1/ha/actions/update-now", headers=headers)
    assert response.status_code == 403, response.text


def test_settings_never_expose_watchtower_token(client, configured_db):
    del configured_db
    login(client)
    patched = client.patch(
        "/api/v1/settings",
        json={"values": {"watchtower_url": "http://watchtower.test:8080", "watchtower_token": _SECRET}},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    listed = client.get("/api/v1/settings", headers=csrf(client))
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["settings"] if item["key"] == "watchtower_token")
    assert row["set"] is True
    assert row.get("value") in (None, "")
    assert _SECRET not in listed.text


def test_update_now_502_response_includes_code(client, configured_db):
    del configured_db
    login(client)
    headers = _bearer(client, ["write"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    import mediatracker.core.watchtower as wt

    original = wt.request_update_now

    def wrapped(db, settings, *, transport=None):
        return original(db, settings, transport=httpx.MockTransport(handler))

    wt.request_update_now = wrapped
    try:
        response = client.post("/api/v1/ha/actions/update-now", headers=headers)
    finally:
        wt.request_update_now = original

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["code"] == "watchtower_error"
    assert body["title"] == "Watchtower"
