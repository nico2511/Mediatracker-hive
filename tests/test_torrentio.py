"""Torrentio: keyless Stremio addon, magnets, cache, and Comet hash dedup."""

import json
from pathlib import Path

import httpx
import pytest

from mediatracker.core.pipeline.unlock import _magnet_keys
from mediatracker.library.release_name import file_source_for_link
from mediatracker.providers.base import EpisodeQuery, LinkCandidate, SourceFetchError, SourcePreferences
from mediatracker.providers.sources.comet import CometPathCache
from mediatracker.providers.sources.loading import source_base_url
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.torrentio import (
    TorrentioProvider,
    TorrentioSource,
    drop_hashes_seen_in_comet,
    shared_torrentio_cache,
)

FIXTURES = Path(__file__).parent / "fixtures" / "torrentio"
SERIES_DEMO = EpisodeQuery(
    title="Labo Démo", year=2026, media_type="series", imdb_id="tt0000099", season=1, episode=1
)
PREFS = SourcePreferences(quality="HD", language="VFF", allow_fallback=False)
BASE = "https://torrentio-fictif.example"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_links_for_builds_magnets_and_skips_debrid_config_in_url():
    fixture_body = json.dumps(_load("demo_series_s01e01.json"))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert "/" not in request.url.path.replace("/stream/", "X") or request.url.path.startswith("/stream/")
        assert "realdebrid" not in request.url.path.lower()
        assert "alldebrid" not in request.url.path.lower()
        return httpx.Response(200, text=fixture_body)

    cache = CometPathCache(revalidate_seconds=3600.0)
    cache.bind_domain(BASE)
    source = TorrentioSource(
        BASE,
        transport=httpx.MockTransport(handler),
        cache=cache,
        pacer=SourcePacer(0),
    )
    try:
        chosen = source.links_for(SERIES_DEMO, PREFS)
    finally:
        source.close()
    assert len(chosen) == 2
    assert chosen[0].external_id == "torrentio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0"
    assert chosen[0].url.startswith("magnet:?xt=urn:btih:")
    assert chosen[0].quality == "1080p"
    assert chosen[0].language == "MULTI"
    assert seen == ["/stream/series/tt0000099:1:1.json"]


def test_path_cache_reuses_response_for_one_hour():
    fixture_body = json.dumps(_load("demo_series_s01e01.json"))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=fixture_body)

    cache = shared_torrentio_cache(BASE)
    cache.bind_domain(BASE)
    source = TorrentioSource(BASE, transport=httpx.MockTransport(handler), cache=cache, pacer=SourcePacer(0))
    try:
        source.links_for(SERIES_DEMO, PREFS)
        source.links_for(SERIES_DEMO, PREFS)
    finally:
        source.close()
    assert calls["n"] == 1


def test_403_raises_without_crashing_pipeline():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error":"forbidden"}')

    source = TorrentioSource(
        BASE,
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    try:
        with pytest.raises(SourceFetchError) as exc:
            source.links_for(SERIES_DEMO, PREFS)
        assert exc.value.code == "TORRENTIO_FORBIDDEN"
    finally:
        source.close()


def test_drop_hashes_seen_in_comet():
    duplicate = LinkCandidate(
        url="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        kind="magnet",
        quality="1080p",
        language="VFF",
        external_id="torrentio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
    )
    other = LinkCandidate(
        url="magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc",
        kind="magnet",
        quality="1080p",
        language="VFF",
        external_id="torrentio:cccccccccccccccccccccccccccccccccccccccc:0",
    )
    comet_hashes = _magnet_keys(
        "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "comet:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
    )
    kept = drop_hashes_seen_in_comet([duplicate, other], comet_hashes)
    assert [item.external_id for item in kept] == ["torrentio:cccccccccccccccccccccccccccccccccccccccc:0"]


def test_open_episode_with_empty_base_url_returns_none(app):
    settings = app.state.settings
    assert TorrentioProvider.open_episode(settings, base_url="") is None
    assert TorrentioProvider.open_episode(settings, base_url="   ") is None


def test_source_base_url_honours_explicit_empty_setting(client, app):
    from mediatracker.db.models import Setting
    from mediatracker.db.session import session_scope

    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "torrentio_base_url")
        assert row is not None
        row.value = ""
        db.commit()
        assert source_base_url(db, app.state.settings, "torrentio") == ""


def test_empty_base_url_makes_no_http_requests(client, app):
    from mediatracker.db.models import Setting
    from mediatracker.db.session import session_scope

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(500)

    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "torrentio_base_url")
        assert row is not None
        row.value = ""
        db.commit()
        base = source_base_url(db, settings, "torrentio")
        source = TorrentioProvider.open_episode(settings, base_url=base, transport=httpx.MockTransport(handler))
    assert base == ""
    assert source is None
    assert calls == []


def test_file_source_for_link_labels_torrentio_magnets():
    class _Link:
        kind = "magnet"
        external_id = "torrentio:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0"

    assert file_source_for_link(_Link()) == "torrentio"
