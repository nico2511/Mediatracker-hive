"""W5 — Wawacity domain sync (backend, mode Proposer)."""

import httpx
import pytest
from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.wawacity_domain import (
    CANDIDATE_KEY,
    PROPOSE_ONLY_KEY,
    DomainValidationError,
    probe_homepage,
    redirect_candidate_base,
    static_validate_candidate,
)
from mediatracker.db.models import Event, Setting
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource

OLD_BASE = "https://wawacity-old.example"
NEW_BASE = "https://wawacity-new.example"


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


def test_redirect_candidate_base_detects_cross_host_302():
    request = httpx.Request("GET", f"{OLD_BASE}/search")
    first = httpx.Response(302, headers={"Location": f"{NEW_BASE}/search"}, request=request)
    final = httpx.Response(200, text="ok", request=httpx.Request("GET", f"{NEW_BASE}/search"))
    final.history = [first]
    assert redirect_candidate_base(str(request.url), final, OLD_BASE) == NEW_BASE


def test_static_validation_rejects_http_ip_and_file_host():
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("http://wawacity-bad.example")
    assert exc_info.value.code == "HTTP"
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("https://127.0.0.1")
    assert exc_info.value.code == "HOST"
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("https://8.8.8.8")
    assert exc_info.value.code == "HOST"
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("https://[2001:db8::1]")
    assert exc_info.value.code == "HOST"
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("https://user@wawacity-new.example")
    assert exc_info.value.code == "HOST"
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("https://wawacity-new.example:8443")
    assert exc_info.value.code == "HOST"
    with pytest.raises(DomainValidationError) as exc_info:
        static_validate_candidate("https://wawa.1fichier.com")
    assert exc_info.value.code == "FILE_HOST"


def test_redirect_candidate_base_rejects_userinfo_and_nonstandard_port():
    request = httpx.Request("GET", f"{OLD_BASE}/search")

    def _final(location: str) -> httpx.Response:
        first = httpx.Response(302, headers={"Location": location}, request=request)
        final = httpx.Response(200, text="ok", request=httpx.Request("GET", location))
        final.history = [first]
        return final

    assert redirect_candidate_base(str(request.url), _final("https://user@wawacity-new.example/"), OLD_BASE) is None
    assert redirect_candidate_base(str(request.url), _final("https://wawacity-new.example:8443/"), OLD_BASE) is None
    assert redirect_candidate_base(str(request.url), _final("https://8.8.8.8/"), OLD_BASE) is None


def test_probe_homepage_performs_single_get(client, app):
    del client
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text="<html></html>")

    probe_homepage(
        NEW_BASE,
        user_agent="AgentFictif/1.0",
        transport=httpx.MockTransport(handler),
    )
    assert len(calls) == 1
    assert calls[0] == f"{NEW_BASE}/"


def test_scan_redirect_proposes_candidate_without_extra_request(client, app, settings):
    del client
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)
        _set_setting(db, PROPOSE_ONLY_KEY, "true")
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.host == "wawacity-old.example":
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
        assert len(calls) == 2
        row = db.get(Setting, CANDIDATE_KEY)
        assert row is not None and row.value == NEW_BASE
        event = db.scalar(select(Event).where(Event.type == "source.domain_proposed"))
        assert event is not None
        assert event.payload.get("host") == "wawacity-new.example"
    finally:
        db.close()


def test_apply_then_revert(client, app, settings):
    login(client)
    db = app.state.session_factory()
    try:
        _set_wawacity(db, OLD_BASE)
        db.add(Setting(key=CANDIDATE_KEY, value=NEW_BASE, is_secret=False))
        db.commit()
    finally:
        db.close()

    homepage_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        homepage_calls.append(str(request.url))
        return httpx.Response(200, text="<html></html>")

    app.state.wawacity_domain_transport = httpx.MockTransport(handler)

    applied = client.post("/api/v1/wawacity/domain/apply", headers=csrf(client))
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["current"] == NEW_BASE
    assert body["candidate"] is None
    assert body["previous"] == OLD_BASE
    assert len(homepage_calls) == 1

    reverted = client.post("/api/v1/wawacity/domain/revert", headers=csrf(client))
    assert reverted.status_code == 200, reverted.text
    body = reverted.json()
    assert body["current"] == OLD_BASE
    assert body["previous"] is None

    db = app.state.session_factory()
    try:
        types = {row.type for row in db.scalars(select(Event)).all()}
        assert "source.domain_applied" in types
        assert "source.domain_reverted" in types
    finally:
        db.close()
