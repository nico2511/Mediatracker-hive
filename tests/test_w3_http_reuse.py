"""W3 — shared outbound HTTP, Comet pacer, and path cache."""

import json

import httpx

from mediatracker.core.outbound_http import borrow_http_client, close_outbound_http_clients
from mediatracker.providers.base import EpisodeQuery, SourcePreferences
from mediatracker.providers.sources.comet import (
    CometPathCache,
    CometSource,
    shared_comet_pacer,
)
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource

BASE = "https://catalogue-fictif.example"
COMET = "https://comet-fictif.example"
SERIES_DEMO = EpisodeQuery(
    title="Brume Azur",
    year=2026,
    media_type="series",
    imdb_id="tt0000002",
    season=1,
    episode=1,
)
PREFS = SourcePreferences(quality="HD", language="VFF", allow_fallback=False)


def test_wawacity_reuses_the_same_httpx_client():
    close_outbound_http_clients()
    config = WawacityConfig(base_url=BASE, user_agent="AgentFictif/1.0")
    first = WawacitySource(config, pacer=SourcePacer(0))
    second = WawacitySource(config, pacer=SourcePacer(0))
    assert first._client is second._client
    assert not first._owns_client
    first.close()
    second.close()
    assert not first._client.is_closed
    close_outbound_http_clients()


def test_comet_open_episode_shares_pacer_and_http():
    close_outbound_http_clients()
    pacer_a = shared_comet_pacer(COMET, 2.5)
    pacer_b = shared_comet_pacer(COMET, 2.5)
    assert pacer_a is pacer_b
    pacer_other = shared_comet_pacer(COMET, 3.0)
    assert pacer_other is not pacer_a
    pacer_other_host = shared_comet_pacer("https://autre-comet.example", 2.5)
    assert pacer_other_host is not pacer_a

    source_a = CometSource(COMET, pacer=pacer_a)
    source_b = CometSource(COMET, pacer=pacer_b)
    assert source_a._client is source_b._client
    assert not source_a._owns_client
    source_a.close()
    source_b.close()
    assert not source_a._client.is_closed
    close_outbound_http_clients()


def test_comet_path_cache_clears_when_domain_changes():
    cache = CometPathCache(max_entries=8, revalidate_seconds=3600.0)
    cache.bind_domain(COMET)
    cache.set("stream/movie/tt1.json", "{}")
    cache.bind_domain("https://autre-comet.example")
    assert cache.get("stream/movie/tt1.json") is None


def test_comet_path_cache_revalidates_after_thirty_days():
    clock = {"now": 0.0}

    def tick() -> float:
        return clock["now"]

    cache = CometPathCache(max_entries=8, revalidate_seconds=30 * 86400.0, clock=tick)
    cache.bind_domain(COMET)
    cache.set("stream/series/tt1:1:1.json", '{"streams":[]}')
    clock["now"] = 30 * 86400.0 - 1.0
    assert cache.get("stream/series/tt1:1:1.json") == '{"streams":[]}'
    clock["now"] = 30 * 86400.0
    assert cache.get("stream/series/tt1:1:1.json") is None


def test_comet_path_cache_is_bounded():
    cache = CometPathCache(max_entries=2, revalidate_seconds=3600.0)
    cache.bind_domain(COMET)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.set("c", "3")
    assert cache.get("a") is None
    assert cache.get("b") == "2"
    assert cache.get("c") == "3"


def test_comet_fetch_uses_path_cache(monkeypatch):
    close_outbound_http_clients()
    body = json.dumps({"streams": []})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=body)

    cache = CometPathCache(max_entries=16, revalidate_seconds=3600.0)
    cache.bind_domain(COMET)
    source = CometSource(
        COMET,
        transport=httpx.MockTransport(handler),
        cache=cache,
        pacer=SourcePacer(0),
    )
    try:
        source.links_for(SERIES_DEMO, PREFS)
        source.links_for(SERIES_DEMO, PREFS)
    finally:
        source.close()
    assert len(seen) == 1
    close_outbound_http_clients()


def test_borrow_http_client_uses_factory_when_monkeypatched():
    close_outbound_http_clients()
    created: list[object] = []

    class FakeClient:
        is_closed = False

        def close(self) -> None:
            self.is_closed = True

    def factory(**_kwargs):
        client = FakeClient()
        created.append(client)
        return client

    first, owned_a = borrow_http_client(("svc", "host"), factory)
    second, owned_b = borrow_http_client(("svc", "host"), factory)
    assert owned_a and owned_b
    assert first is not second
    assert len(created) == 2
    close_outbound_http_clients()
