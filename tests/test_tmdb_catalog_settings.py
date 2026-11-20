"""Réglages catalogue TMDB : paramètres API, filtre serveur, cache, identification intacte."""

from __future__ import annotations

import httpx
import pytest
from tests.conftest import login

from mediatracker.core.tmdb.catalog_settings import (
    filter_hits_by_original_language,
    validate_display_language,
    validate_original_languages,
    validate_region,
)
from mediatracker.core.tmdb_trial_cache import cache_key, clear_tmdb_trial_cache
from mediatracker.integrations.tmdb import TmdbClient, TmdbHit


def test_validate_tmdb_catalog_setting_formats():
    assert validate_display_language("fr-FR") == "fr-FR"
    assert validate_region("fr") == "FR"
    assert validate_original_languages("fr,en,ja") == "fr,en,ja"
    with pytest.raises(ValueError):
        validate_display_language("french")
    with pytest.raises(ValueError):
        validate_region("FRA")


def test_filter_hits_by_original_language():
    hits = [
        TmdbHit(1, "film", "Alpha", 2020, None, None, original_language="fr"),
        TmdbHit(2, "film", "Beta", 2021, None, None, original_language="en"),
        TmdbHit(3, "film", "Gamma", 2022, None, None, original_language=None),
    ]
    filtered = filter_hits_by_original_language(hits, ("fr",))
    assert [row.tmdb_id for row in filtered] == [1]
    assert filter_hits_by_original_language(hits, ()) == hits


def test_cache_key_includes_catalog_settings():
    base = {"page": 1}
    fragment = {
        "display_language": "en-US",
        "region": "US",
        "original_languages": ("ja",),
    }
    assert cache_key("tmdb-trial-browse", {**base, **fragment}) != cache_key("tmdb-trial-browse", base)


class _RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/3")
        params = {key: value for key, value in request.url.params.multi_items()}
        self.calls.append((path, params))
        if path == "/discover/movie":
            return httpx.Response(
                200,
                json={
                    "page": 1,
                    "total_pages": 1,
                    "results": [
                        {
                            "id": 1001,
                            "title": "Œuvre Alpha",
                            "original_language": "fr",
                            "poster_path": "/a.jpg",
                            "release_date": "2026-05-01",
                        }
                    ],
                },
            )
        if path == "/genre/movie/list":
            return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drame"}]})
        if path == "/watch/providers/movie":
            return httpx.Response(
                200,
                json={"results": {"FR": {"flatrate": [{"provider_id": 8, "provider_name": "Plateforme Démo"}]}}},
            )
        if path.startswith("/search/"):
            return httpx.Response(
                200,
                json={
                    "page": 1,
                    "total_pages": 1,
                    "results": [
                        {
                            "id": 2001,
                            "title": "Œuvre Beta",
                            "original_language": "en",
                            "poster_path": "/b.jpg",
                            "release_date": "2026-05-01",
                        },
                        {
                            "id": 2002,
                            "title": "Œuvre Gamma",
                            "original_language": "fr",
                            "poster_path": "/c.jpg",
                            "release_date": "2026-04-01",
                        },
                    ],
                },
            )
        raise AssertionError(f"unexpected path {path}")

    def client(self) -> TmdbClient:
        return TmdbClient("tmdb-test", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def recording_client():
    clear_tmdb_trial_cache()
    transport = _RecordingTransport()
    return transport, transport.client()


def test_discover_sends_with_original_language(client, app, monkeypatch):
    login(client)
    transport = _RecordingTransport()
    mock = TmdbClient("tmdb-test", language="de-DE", transport=httpx.MockTransport(transport.handler))
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    db = app.state.session_factory()
    try:
        from mediatracker.settings_store import update_settings

        update_settings(
            db,
            app.state.settings,
            {
                "tmdb_display_language": "de-DE",
                "tmdb_region": "DE",
                "tmdb_original_languages": "fr,en",
            },
        )
    finally:
        db.close()

    response = client.get(
        "/api/v1/tmdb-trial/browse",
        params={"type": "film", "release_kind": "streaming", "page": 1},
    )
    assert response.status_code == 200, response.text
    params = next(p for path, p in reversed(transport.calls) if path == "/discover/movie")
    assert params.get("language") == "de-DE"
    assert params.get("region") == "DE"
    assert params.get("watch_region") == "DE"
    assert params.get("with_original_language") == "fr|en"


def test_browse_search_filtered_metadata_search_not(client, app, monkeypatch, recording_client):
    login(client)
    transport, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    db = app.state.session_factory()
    try:
        from mediatracker.settings_store import update_settings

        update_settings(
            db,
            app.state.settings,
            {"tmdb_original_languages": "fr"},
        )
    finally:
        db.close()

    browse = client.get("/api/v1/tmdb-trial/browse", params={"type": "film", "q": "demo", "page": 1})
    assert browse.status_code == 200
    assert len(browse.json()["items"]) == 1
    assert browse.json()["items"][0]["title"] == "Œuvre Gamma"

    monkeypatch.setattr("mediatracker.api.v1.metadata.borrow_client", lambda db, settings: (mock, False))
    meta = client.get("/api/v1/metadata/tmdb/search", params={"q": "demo", "type": "film"})
    assert meta.status_code == 200
    titles = {row["title"] for row in meta.json()["items"]}
    assert "Œuvre Beta" in titles
    assert "Œuvre Gamma" in titles
