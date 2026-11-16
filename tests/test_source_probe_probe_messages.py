"""Messages du testeur de sources : profil, cache, TMDB."""

import json
from pathlib import Path

import httpx
from tests.conftest import csrf, login

from mediatracker.core.quality_profile import Profile
from mediatracker.core.source_probe import SourceProbeTarget, _probe_episode, _stub_item, _stub_media
from mediatracker.providers.base import SourcePreferences
from mediatracker.providers.sources.comet import CometPathCache
from mediatracker.providers.sources.stremio_addons import StremioAddonSource
from mediatracker.providers.sources.stremio_addons_config import StremioAddonEntry
from mediatracker.providers.sources.throttle import SourcePacer


def test_stremio_profile_reason_lists_seen_streams():
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "stremio_addons" / "demo_movie_ddl.json").read_text(encoding="utf-8")
    )
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
        StremioAddonEntry(url="https://addon-alpha.example", enabled=True),
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    source.quality_profile = strict
    try:
        from mediatracker.providers.base import EpisodeQuery

        query = EpisodeQuery(
            title="Film Démo",
            year=2026,
            media_type="film",
            imdb_id="tt9999999",
        )
        assert source.links_for(query, SourcePreferences()) == []
        assert source.last_empty_reason is not None
        assert "flux vu" in source.last_empty_reason
        assert "VO 4K seulement" in source.last_empty_reason
        assert "Aucune source" not in source.last_empty_reason
    finally:
        source.close()


def test_probe_episode_marks_cache_on_empty_reason(client, app, monkeypatch):
    strict = Profile(
        name="Profil test",
        languages=("VFF",),
        min_resolution="1080p",
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )

    class FakeSource:
        quality_profile = None
        last_empty_reason = "Aucun flux ne correspond au profil « Profil test » (1 flux vu)."
        last_json_from_cache = True

        def links_for(self, _query, _prefs):
            return []

    monkeypatch.setattr(
        "mediatracker.core.source_probe.episode_source",
        lambda *_a, **_k: FakeSource(),
    )
    monkeypatch.setattr(
        "mediatracker.core.source_probe.close_source",
        lambda _s: None,
    )
    target = SourceProbeTarget("Film Démo", "film", None, None, "tt9999999")
    media = _stub_media(target)
    item = _stub_item(target)
    db = app.state.session_factory()
    try:
        outcome = _probe_episode(
            db,
            app.state.settings,
            "comet",
            target,
            media,
            item,
            spec=strict,
            comet_prefs=SourcePreferences(),
        )
    finally:
        db.close()
    assert outcome.reason is not None
    assert "résultat en cache" in outcome.reason
    assert "correspond au profil" in outcome.reason


def test_parse_probe_query_accepts_tmdb_id():
    from mediatracker.core.source_probe import parse_probe_query

    target = parse_probe_query("Film Démo", tmdb_id=900101)
    assert target.tmdb_id == 900101
    assert target.imdb_id is None


def test_probe_endpoint_still_accepts_legacy_imdb(client, monkeypatch):
    login(client)

    def fake_probe(_db, _settings, target, **_kwargs):
        assert target.imdb_id == "tt9999999"
        from mediatracker.core.source_probe import ProbeRunResult

        return ProbeRunResult([], None)

    monkeypatch.setattr("mediatracker.api.v1.settings.probe_download_sources", fake_probe)
    response = client.post(
        "/api/v1/settings/sources/probe",
        json={"query": "Film Démo", "imdb_id": "9999999"},
        headers=csrf(client),
    )
    assert response.status_code == 200


def test_probe_endpoint_returns_profile_name(client, monkeypatch):
    login(client)

    def fake_probe(_db, _settings, _target, **_kwargs):
        from mediatracker.core.source_probe import ProbeRunResult, SourceProbeRow

        return ProbeRunResult(
            [
                SourceProbeRow(
                    source="comet",
                    label="Comet",
                    issue="empty",
                    link_count=0,
                    reason=None,
                    duration_ms=1,
                )
            ],
            "Profil Démo",
        )

    monkeypatch.setattr("mediatracker.api.v1.settings.probe_download_sources", fake_probe)
    response = client.post(
        "/api/v1/settings/sources/probe",
        json={"query": "Film Démo"},
        headers=csrf(client),
    )
    assert response.status_code == 200
    assert response.json()["profile_name"] == "Profil Démo"