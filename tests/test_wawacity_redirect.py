"""Audit S3d #10 — Wawacity redirect policy (mock transport, no network)."""

import httpx
import pytest
from sqlalchemy import select

from mediatracker.core.wawacity_domain import PROPOSE_ONLY_KEY
from mediatracker.db.models import Event, Setting
from mediatracker.providers.base import SourceFetchError
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.security.wawacity_redirect import (
    WawacityRedirectRejected,
    is_wawacity_brand_host,
    validate_wawacity_redirect_target,
)

OLD_HTTPS = "https://wawacity-old.example-tld"
NEW_HTTPS = "https://wawacity-new.example-tld"
HTTP_BASE = "http://wawacity-old.example-tld"


def test_brand_host_label_rules():
    assert is_wawacity_brand_host("wawacity.example-tld")
    assert is_wawacity_brand_host("www.wawacity.example-tld")
    assert not is_wawacity_brand_host("wawacity.evil.test")
    assert not is_wawacity_brand_host("evil.example-tld")


def test_validate_redirect_rejects_third_party_ip_and_downgrade():
    with pytest.raises(WawacityRedirectRejected):
        validate_wawacity_redirect_target(
            "https://evil.example-tld/page",
            from_url=f"{OLD_HTTPS}/",
            configured_base=OLD_HTTPS,
        )
    with pytest.raises(WawacityRedirectRejected):
        validate_wawacity_redirect_target(
            "https://8.8.8.8/",
            from_url=f"{OLD_HTTPS}/",
            configured_base=OLD_HTTPS,
        )
    with pytest.raises(WawacityRedirectRejected):
        validate_wawacity_redirect_target(
            "https://wawacity.evil.test/",
            from_url=f"{OLD_HTTPS}/",
            configured_base=OLD_HTTPS,
        )
    with pytest.raises(WawacityRedirectRejected) as caught:
        validate_wawacity_redirect_target(
            "http://wawacity-new.example-tld/",
            from_url=f"{OLD_HTTPS}/",
            configured_base=OLD_HTTPS,
        )
    assert "HTTPS vers HTTP" in str(caught.value)


def test_tld_change_followed_without_extra_request_to_blocked_target(client, app, settings):
    del client
    db = app.state.session_factory()
    try:
        row = db.get(Setting, PROPOSE_ONLY_KEY)
        if row is None:
            db.add(Setting(key=PROPOSE_ONLY_KEY, value="true", is_secret=False))
        else:
            row.value = "true"
        db.commit()
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.host == "wawacity-old.example-tld":
                return httpx.Response(
                    302,
                    headers={"Location": f"{NEW_HTTPS}/page"},
                    request=request,
                )
            return httpx.Response(200, text="<html>ok</html>")

        source = WawacitySource(
            WawacityConfig(base_url=OLD_HTTPS, user_agent="AgentFictif/1.0"),
            transport=httpx.MockTransport(handler),
            pacer=SourcePacer(0, sleeper=lambda _seconds: None),
            redirect_db=db,
            redirect_settings=settings,
        )
        try:
            body = source._get(f"{OLD_HTTPS}/films.html")
        finally:
            source.close()
        assert body == "<html>ok</html>"
        assert len(calls) == 2
        assert calls[1].startswith(NEW_HTTPS)
    finally:
        db.close()


def test_blocked_redirect_records_event_and_skips_target_request(client, app, settings):
    del client
    db = app.state.session_factory()
    try:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example-tld/steal"},
                request=request,
            )

        source = WawacitySource(
            WawacityConfig(base_url=OLD_HTTPS, user_agent="AgentFictif/1.0"),
            transport=httpx.MockTransport(handler),
            pacer=SourcePacer(0, sleeper=lambda _seconds: None),
            redirect_db=db,
            redirect_settings=settings,
        )
        try:
            with pytest.raises(SourceFetchError) as caught:
                source._get(f"{OLD_HTTPS}/films.html")
            assert caught.value.code == "REDIRECT_BLOCKED"
        finally:
            source.close()
        assert len(calls) == 1
        event = db.scalar(select(Event).where(Event.type == "source.redirect_blocked"))
        assert event is not None
        assert event.payload.get("host") == "evil.example-tld"
    finally:
        db.close()


def test_http_base_allows_initial_http_without_following_http_redirect(client, app):
    del client, app
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.scheme == "http":
            return httpx.Response(
                302,
                headers={"Location": "http://wawacity-other.example-tld/"},
                request=request,
            )
        return httpx.Response(200, text="ok")

    source = WawacitySource(
        WawacityConfig(base_url=HTTP_BASE, user_agent="AgentFictif/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    try:
        with pytest.raises(SourceFetchError) as caught:
            source._get(f"{HTTP_BASE}/")
        assert caught.value.code == "REDIRECT_BLOCKED"
    finally:
        source.close()
    assert len(calls) == 1
