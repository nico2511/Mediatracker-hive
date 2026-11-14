"""W6 — Wawacity domain auto-migration (backend)."""

import json

import httpx
from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.wawacity_domain import (
    AUTO_BLOCKED_HOST_KEY,
    CANDIDATE_KEY,
    LAST_MIGRATION_KEY,
    PREVIOUS_KEY,
    PROPOSE_ONLY_KEY,
)
from mediatracker.db.models import Event, Setting
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource

OLD_BASE = "https://wawacity-test-old.example"
NEW_BASE = "https://wawacity-test-new.example"


def _set_wawacity(db, url: str) -> None:
    row = db.get(Setting, "wawacity_base_url")
    if row is None:
        db.add(Setting(key="wawacity_base_url", value=url, is_secret=False))
    else:
        row.value = url
    db.commit()


def _set_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False))
    else:
        row.value = value
    db.commit()


def _source(handler, db, settings) -> WawacitySource:
    return WawacitySource(
        WawacityConfig(base_url=OLD_BASE, user_agent="AgentFictif/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
        redirect_db=db,
        redirect_settings=settings,
    )


def test_auto_apply_after_redirect_passes_guards(client, app, settings):
    del client
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.host == "wawacity-test-old.example":
                return httpx.Response(
                    302,
                    headers={"Location": f"{NEW_BASE}/page"},
                    request=request,
                )
            return httpx.Response(200, text="<html>ok</html>")

        source = _source(handler, db, settings)
        try:
            source._get(f"{OLD_BASE}/films.html")
        finally:
            source.close()
        assert db.get(Setting, "wawacity_base_url").value == NEW_BASE
        assert db.get(Setting, CANDIDATE_KEY).value in (None, "")
        migration = json.loads(db.get(Setting, LAST_MIGRATION_KEY).value)
        assert migration["from_base_url"] == OLD_BASE
        assert migration["to_base_url"] == NEW_BASE
        assert migration["at"]
        event = db.scalar(select(Event).where(Event.type == "source.domain_auto_migrated"))
        assert event is not None
        assert event.payload.get("previous_host") == "wawacity-test-old.example"
        assert event.payload.get("host") == "wawacity-test-new.example"
        assert event.payload.get("reason")
        assert event.payload.get("migrated_at")
        assert any(url.startswith(NEW_BASE) for url in calls)
    finally:
        db.close()


def test_auto_apply_refused_when_guard_fails(client, app, settings):
    del client
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.host == "wawacity-test-old.example":
                return httpx.Response(
                    302,
                    headers={"Location": f"{NEW_BASE}/page"},
                    request=req,
                )
            if req.url.path == "/":
                return httpx.Response(503, text="down")
            return httpx.Response(200, text="<html></html>")

        source = _source(handler, db, settings)
        try:
            source._get(f"{OLD_BASE}/films.html")
        finally:
            source.close()
        assert db.get(Setting, "wawacity_base_url").value == OLD_BASE
        assert db.scalar(select(Event).where(Event.type == "source.domain_auto_migrated")) is None
    finally:
        db.close()


def test_revert_restores_and_blocks_auto_reapply(client, app, settings):
    login(client)
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)
    finally:
        db.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "wawacity-test-old.example":
            return httpx.Response(
                302,
                headers={"Location": f"{NEW_BASE}/page"},
                request=request,
            )
        return httpx.Response(200, text="<html>ok</html>")

    db = app.state.session_factory()
    try:
        source = _source(handler, db, settings)
        try:
            source._get(f"{OLD_BASE}/films.html")
        finally:
            source.close()
        assert db.get(Setting, "wawacity_base_url").value == NEW_BASE
    finally:
        db.close()

    reverted = client.post("/api/v1/wawacity/domain/revert", headers=csrf(client))
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["current"] == OLD_BASE

    db = app.state.session_factory()
    try:
        assert db.get(Setting, AUTO_BLOCKED_HOST_KEY).value == "wawacity-test-new.example"
        source = _source(handler, db, settings)
        try:
            source._get(f"{OLD_BASE}/films.html")
        finally:
            source.close()
        assert db.get(Setting, "wawacity_base_url").value == OLD_BASE
        assert db.scalar(select(Event).where(Event.type == "source.domain_auto_migrated")) is not None
        count = len(db.scalars(select(Event).where(Event.type == "source.domain_auto_migrated")).all())
        assert count == 1
    finally:
        db.close()


def test_propose_only_mode_still_proposes(client, app, settings):
    del client
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)
        _set_setting(db, PROPOSE_ONLY_KEY, "true")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "wawacity-test-old.example":
                return httpx.Response(
                    302,
                    headers={"Location": f"{NEW_BASE}/page"},
                    request=request,
                )
            return httpx.Response(200, text="<html>ok</html>")

        source = _source(handler, db, settings)
        try:
            source._get(f"{OLD_BASE}/films.html")
        finally:
            source.close()
        assert db.get(Setting, "wawacity_base_url").value == OLD_BASE
        assert db.get(Setting, CANDIDATE_KEY).value == NEW_BASE
        assert db.scalar(select(Event).where(Event.type == "source.domain_proposed")) is not None
        assert db.scalar(select(Event).where(Event.type == "source.domain_auto_migrated")) is None
    finally:
        db.close()


def test_manual_apply_clears_auto_block(client, app, settings):
    login(client)
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)
        db.add(Setting(key=CANDIDATE_KEY, value=NEW_BASE, is_secret=False))
        db.add(Setting(key=PREVIOUS_KEY, value=OLD_BASE, is_secret=False))
        db.add(Setting(key=AUTO_BLOCKED_HOST_KEY, value="wawacity-test-new.example", is_secret=False))
        db.commit()
    finally:
        db.close()

    app.state.wawacity_domain_transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text="<html></html>")
    )
    applied = client.post("/api/v1/wawacity/domain/apply", headers=csrf(client))
    assert applied.status_code == 200, applied.text

    db = app.state.session_factory()
    try:
        blocked = db.get(Setting, AUTO_BLOCKED_HOST_KEY)
        assert blocked is None or not blocked.value
    finally:
        db.close()
