from urllib.parse import parse_qs

import httpx
import pytest

from mediatracker.providers.base import LinkCandidate
from mediatracker.providers.debrid.alldebrid import (
    AUTH_BLOCKED_MESSAGE,
    AllDebridClient,
    AllDebridConfig,
    AllDebridError,
    AllDebridProvider,
)


def _client(handler, **kwargs):
    return AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None, **kwargs)


def test_redirector_follows_301_and_keeps_the_post_body():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.alldebrid.com":
            return httpx.Response(301, headers={"location": "https://apislow.alldebrid.com/v4/link/redirector"})
        return httpx.Response(
            200,
            json={"status": "success", "data": {"links": ["https://rapidgator.net/file/abc"]}},
        )

    links = _client(handler).redirector("https://dl-protect.link/abc")
    assert links == ["https://rapidgator.net/file/abc"]
    assert [request.url.host for request in seen] == ["api.alldebrid.com", "apislow.alldebrid.com"]
    assert all(request.method == "POST" for request in seen)
    assert all(request.headers["authorization"] == "Bearer test-key" for request in seen)
    for request in seen:
        assert parse_qs(request.content.decode())["link"] == ["https://dl-protect.link/abc"]


def test_off_site_redirect_is_refused():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://evil.example/steal"})

    with pytest.raises(AllDebridError) as caught:
        _client(handler).redirector("https://dl-protect.link/abc")
    assert caught.value.code == "REDIRECT_BLOCKED"


def test_unlock_returns_filename_and_host_unavailable_is_retryable():
    def ok(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.alldebrid.com":
            return httpx.Response(301, headers={"location": "https://apislow.alldebrid.com/v4/link/unlock"})
        assert request.url.host == "apislow.alldebrid.com"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "link": "https://cdn.example/secret.mkv",
                    "filename": "Episode.mkv",
                    "filesize": 20,
                    "host": "rapidgator",
                },
            },
        )

    direct = _client(ok).unlock("https://rapidgator.net/file/1")
    assert direct.filename == "Episode.mkv"
    assert direct.host == "rapidgator"
    assert direct.url.endswith("secret.mkv")

    calls = {"n": 0}

    def down(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"status": "error", "error": {"code": "LINK_HOST_UNAVAILABLE", "message": "Host unavailable"}},
        )

    with pytest.raises(AllDebridError) as caught:
        _client(down).unlock("https://turbobit.net/file/1")
    assert caught.value.code == "LINK_HOST_UNAVAILABLE"
    assert caught.value.retryable is True
    assert calls["n"] == 1


def test_delayed_unlock_and_auth_blocked_health():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/link/unlock"):
            return httpx.Response(200, json={"status": "success", "data": {"delayed": "42"}})
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"status": "success", "data": {}})
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "link": "https://cdn.example/a.mkv",
                    "filename": "a.mkv",
                    "filesize": 5,
                    "host": "turbobit",
                },
            },
        )

    direct = _client(handler).unlock("https://rapidgator.net/x", poll_timeout=30, poll_interval=0)
    assert direct.filename == "a.mkv"
    assert calls["n"] == 2

    def blocked(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "error", "error": {"code": "AUTH_BLOCKED", "message": "confirm email"}},
        )

    provider = AllDebridProvider(AllDebridConfig(api_key="test-key"))
    provider.client = _client(blocked)
    health = provider.health()
    assert health.status == "down"
    assert health.code == "AUTH_BLOCKED"
    assert health.message == AUTH_BLOCKED_MESSAGE


def test_supports_filters_down_hosters():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "hosts": {
                        "rapidgator.net": {"status": True},
                        "turbobit.net": {"status": "down"},
                    }
                },
            },
        )

    provider = AllDebridProvider(AllDebridConfig(api_key="test-key"))
    provider.client = _client(handler)
    assert provider.supports(LinkCandidate(url="https://rg", kind="ddl_hoster", hoster="rapidgator", external_id="a"))
    assert not provider.supports(LinkCandidate(url="https://tb", kind="ddl_hoster", hoster="turbobit", external_id="b"))
    assert provider.supports(LinkCandidate(url="https://dl-protect.link/x", kind="ddl_protected", external_id="c"))
