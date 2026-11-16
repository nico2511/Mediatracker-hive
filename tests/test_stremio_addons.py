"""Addons Stremio génériques : torrent, DDL, info seule, secrets retirés des URL."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from mediatracker.providers.base import EpisodeQuery, SourcePreferences
from mediatracker.providers.sources.comet import CometPathCache
from mediatracker.providers.sources.stremio_addons import (
    StremioAddonSource,
    StremioAddonsProvider,
    select_stremio_addon_streams,
)
from mediatracker.providers.sources.stremio_addons_config import (
    StremioAddonEntry,
    strip_secret_segments,
    validate_stremio_addon_url,
)
from mediatracker.providers.sources.throttle import SourcePacer

FIXTURES = Path(__file__).parent / "fixtures" / "stremio_addons"
FILM = EpisodeQuery(title="Fiction Labo", year=2026, media_type="film", imdb_id="tt9999999")
PREFS = SourcePreferences(quality="HD", language="MULTI", allow_fallback=True, quality_fallback=["4K"])
BASE = "https://addon-alpha.example"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_select_streams_torrent_ignores_web_player():
    chosen = select_stremio_addon_streams(_load("demo_movie_torrent.json"), FILM, PREFS)
    assert len(chosen) == 1
    assert chosen[0].kind == "magnet"
    assert chosen[0].external_id.startswith("stremio_addons:")


def test_select_streams_ddl_rejects_uptobox_and_info_rows():
    chosen = select_stremio_addon_streams(_load("demo_movie_ddl.json"), FILM, PREFS)
    assert len(chosen) == 1
    assert chosen[0].kind == "ddl_hoster"
    assert chosen[0].hoster == "rapidgator"
    assert chosen[0].url.startswith("https://debrideur-exemple.test/f/")


def test_strip_secret_query_from_addon_url():
    dirty = "https://addon-fictif.example/manifest.json?alldebrid_key=supersecret&foo=1"
    clean = validate_stremio_addon_url(dirty)
    assert "alldebrid_key" not in clean
    assert clean == "https://addon-fictif.example/manifest.json"
    stripped = strip_secret_segments(dirty)
    assert "alldebrid_key" not in stripped
    assert "foo=1" in stripped


def test_links_for_one_request_per_addon_no_secret_in_url():
    fixture_body = json.dumps(_load("demo_movie_ddl.json"))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert "alldebrid_key" not in request.url.query.decode().lower()
        return httpx.Response(200, text=fixture_body)

    source = StremioAddonSource(
        StremioAddonEntry(url=BASE, enabled=True),
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    try:
        chosen = source.links_for(FILM, PREFS)
    finally:
        source.close()
    assert len(chosen) == 1
    assert seen == [f"{BASE}/stream/movie/tt9999999.json"]


def test_profile_refusal_message():
    payload = _load("demo_movie_ddl.json")
    from mediatracker.core.quality_profile import Profile

    strict = Profile(
        name="VO 4K seulement",
        languages=("VO",),
        min_resolution="4K",
        max_resolution="4K",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(payload))

    source = StremioAddonSource(
        StremioAddonEntry(url=BASE, enabled=True),
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    source.quality_profile = strict
    try:
        assert source.links_for(FILM, PREFS) == []
    finally:
        source.close()
    assert source.last_empty_reason is not None
    assert "flux vu" in source.last_empty_reason
    assert "VO 4K seulement" in source.last_empty_reason


def test_open_episode_none_when_no_enabled_addon(app):
    settings = app.state.settings
    assert StremioAddonsProvider.open_episode(settings, addons=[]) is None
    assert (
        StremioAddonsProvider.open_episode(
            settings, addons=[StremioAddonEntry(url=BASE, enabled=False)]
        )
        is None
    )


def test_decode_wrapped_ddl_still_works():
    wrapped = (
        "https://addon-fictif.example/resolve-stream/video.mkv?"
        + "url="
        + base64.urlsafe_b64encode(b"https://debrideur-exemple.test/f/WRAP").decode().rstrip("=")
    )
    payload = {
        "streams": [
            {
                "url": wrapped,
                "name": "⚡",
                "description": "📦 Hébergeur : 1fichier",
                "behaviorHints": {"filename": "Fiction.Labo.2026.MULTI.1080p", "notWebReady": True},
            }
        ]
    }
    chosen = select_stremio_addon_streams(payload, FILM, PREFS)
    assert len(chosen) == 1
    assert chosen[0].hoster == "1fichier"


@pytest.mark.parametrize(
    "url",
    [
        "https://addon-fictif.example/stream/movie/tt9999999.json?torbox_key=abc",
    ],
)
def test_request_rejected_when_secret_remains_in_built_url(url):
    from mediatracker.providers.base import SourceFetchError

    source = StremioAddonSource(
        StremioAddonEntry(url="https://addon-fictif.example", enabled=True),
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, text='{"streams":[]}')),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    with pytest.raises(SourceFetchError):
        source._get(url)  # noqa: SLF001
    source.close()