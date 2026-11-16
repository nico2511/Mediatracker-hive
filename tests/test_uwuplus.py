"""UwU+ : addon Stremio DDL, filtrage WEB/Rescraper, AllDebrid sans clé côté addon."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from mediatracker.core.quality_profile import default_profile
from mediatracker.providers.base import EpisodeQuery, SourceFetchError, SourcePreferences
from mediatracker.providers.sources.comet import CometPathCache
from mediatracker.providers.sources.loading import source_base_url
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.uwuplus import (
    UwuPlusProvider,
    UwuPlusSource,
    select_uwu_streams,
)

FIXTURES = Path(__file__).parent / "fixtures" / "uwuplus"
FILM_DEMO = EpisodeQuery(title="Labo Démo", year=2026, media_type="film", imdb_id="tt0000101")
PREFS = SourcePreferences(quality="HD", language="MULTI", allow_fallback=True, quality_fallback=["4K"])
BASE = "https://uwu-fictif.example"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_select_streams_keeps_ddl_rejects_web_uptobox_and_rescrape():
    chosen = select_uwu_streams(_load("demo_movie_streams.json"), FILM_DEMO, PREFS)
    assert len(chosen) == 2
    assert all(link.kind == "ddl_hoster" for link in chosen)
    assert all(link.url.startswith("https://debrideur-exemple.test/f/") for link in chosen)
    hosters = [link.hoster for link in chosen]
    assert "uptobox" not in hosters
    assert hosters == ["rapidgator", "1fichier"]


def test_external_url_decoded_when_missing_uses_base64_wrapped():
    wrapped = (
        "https://uwu-fictif.example/resolve-stream/video.mkv?"
        + "url="
        + base64.urlsafe_b64encode(b"https://debrideur-exemple.test/f/DECODED").decode().rstrip("=")
    )
    payload = {
        "streams": [
            {
                "url": wrapped,
                "name": "⚡[AD]",
                "description": "📦 Hébergeur : Rapidgator",
                "behaviorHints": {"filename": "Labo.Demo.2026.MULTI.1080p", "notWebReady": True},
            }
        ]
    }
    chosen = select_uwu_streams(payload, FILM_DEMO, PREFS)
    assert len(chosen) == 1
    assert chosen[0].url == "https://debrideur-exemple.test/f/DECODED"


def test_links_for_requests_stream_without_secrets():
    fixture_body = json.dumps(_load("demo_movie_streams.json"))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert "alldebrid_key" not in request.url.query.decode().lower()
        assert "config=" not in str(request.url).lower()
        return httpx.Response(200, text=fixture_body)

    source = UwuPlusSource(
        BASE,
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    try:
        chosen = source.links_for(FILM_DEMO, PREFS)
    finally:
        source.close()
    assert len(chosen) == 2
    assert seen == [f"{BASE}/stream/movie/tt0000101.json"]


def test_open_episode_disabled_when_base_url_empty(app):
    settings = app.state.settings
    assert UwuPlusProvider.open_episode(settings, base_url="") is None
    assert UwuPlusProvider.open_episode(settings, base_url="   ") is None


def test_source_base_url_honours_explicit_empty_setting(client, app):
    from mediatracker.db.models import Setting
    from mediatracker.db.session import session_scope

    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "uwuplus_base_url")
        if row is None:
            row = Setting(key="uwuplus_base_url", value="", is_secret=False)
            db.add(row)
        else:
            row.value = ""
        db.commit()
        assert source_base_url(db, app.state.settings, "uwuplus") == ""


def test_info_only_payload_sets_no_source_reason_under_profile():
    payload = _load("info_no_streams.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(payload))

    source = UwuPlusSource(
        BASE,
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    source.quality_profile = default_profile()
    try:
        assert source.links_for(FILM_DEMO, PREFS) == []
    finally:
        source.close()
    assert source.last_empty_reason == "UwU+ n'a aucune source pour ce titre."


def test_profile_refusal_when_ddl_streams_filtered_out():
    payload = _load("demo_movie_streams.json")
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

    source = UwuPlusSource(
        BASE,
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    source.quality_profile = strict
    try:
        assert source.links_for(FILM_DEMO, PREFS) == []
    finally:
        source.close()
    assert source.last_empty_reason is not None
    assert "ne correspond au profil" in source.last_empty_reason
    assert "VO 4K seulement" in source.last_empty_reason


def test_403_raises_without_crashing_pipeline():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text='{"error":"forbidden"}')

    source = UwuPlusSource(
        BASE,
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    try:
        with pytest.raises(SourceFetchError) as exc:
            source.links_for(FILM_DEMO, PREFS)
        assert exc.value.code == "UWUPLUS_FORBIDDEN"
    finally:
        source.close()