"""Pages essai TMDB : paramètres discover FR, tri, cache, pas d'écriture Media."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select
from tests.conftest import login

from mediatracker.core.tmdb_trial_cache import clear_tmdb_trial_cache
from mediatracker.db.models import LibraryFile, Media
from mediatracker.integrations.tmdb import WATCH_MONETIZATION_TYPES_FR, TmdbClient

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


class _RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/3")
        params = {key: value for key, value in request.url.params.multi_items()}
        self.calls.append((path, params))
        if path in {"/discover/movie", "/discover/tv", "/search/movie", "/search/tv"}:
            kind = "film" if "movie" in path else "series"
            row = (
                {
                    "id": 1001,
                    "title": "Œuvre Alpha",
                    "poster_path": "/a.jpg",
                    "release_date": "2026-05-01",
                    "vote_average": 7.0,
                }
                if kind == "film"
                else {
                    "id": 2002,
                    "name": "Série Beta",
                    "poster_path": "/b.jpg",
                    "first_air_date": "2026-04-10",
                    "vote_average": 8.0,
                }
            )
            return httpx.Response(
                200,
                json={"page": int(params.get("page", 1)), "total_pages": 1, "results": [row]},
            )
        if path == "/genre/movie/list":
            return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drame"}]})
        if path == "/genre/tv/list":
            return httpx.Response(200, json={"genres": [{"id": 18, "name": "Drame"}]})
        if path == "/watch/providers/movie":
            return httpx.Response(
                200,
                json={"results": {"FR": {"flatrate": [{"provider_id": 8, "provider_name": "Plateforme Démo"}]}}},
            )
        raise AssertionError(f"unexpected path {path}")

    def client(self) -> TmdbClient:
        return TmdbClient("tmdb-test", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def recording_client():
    clear_tmdb_trial_cache()
    transport = _RecordingTransport()
    return transport, transport.client()


def _last_discover(calls: list[tuple[str, dict[str, str]]], media: str) -> dict[str, str]:
    path = "/discover/movie" if media == "film" else "/discover/tv"
    for call_path, params in reversed(calls):
        if call_path == path:
            return params
    raise AssertionError(f"no {path} call")


def test_discover_params_cinema_vod_dvd_and_series_fr(recording_client, client, app, monkeypatch):
    login(client)
    transport, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    kinds = [
        ("film", "cinema", "2|3"),
        ("film", "streaming", "4"),
        ("film", "physical", "5"),
        ("series", "new", None),
        ("series", "on_air", None),
    ]
    for media_type, release_kind, release_type in kinds:
        transport.calls.clear()
        response = client.get(
            "/api/v1/tmdb-trial/browse",
            params={"type": media_type, "release_kind": release_kind, "page": 1, "sort": "recent"},
        )
        assert response.status_code == 200, response.text
        params = _last_discover(transport.calls, media_type)
        assert params.get("language") == "fr-FR"
        if media_type == "film":
            assert params.get("region") == "FR"
            assert params.get("with_release_type") == release_type
            assert "release_date.gte" in params
            if release_kind == "streaming":
                assert params.get("watch_region") == "FR"
                assert params.get("with_watch_monetization_types") == WATCH_MONETIZATION_TYPES_FR
                assert params.get("sort_by") == "release_date.desc"
            elif release_kind == "cinema":
                assert "watch_region" not in params
                assert params.get("sort_by") == "release_date.desc"
        else:
            assert params.get("watch_region") == "FR"
            assert params.get("with_watch_monetization_types") == WATCH_MONETIZATION_TYPES_FR
            assert params.get("sort_by") == "first_air_date.desc"
            if release_kind == "on_air":
                assert "air_date.gte" in params
                assert "air_date.lte" in params
                assert "on_the_air" not in [path for path, _ in transport.calls]
            else:
                assert "first_air_date.gte" in params


def test_trial_cards_expose_release_date(recording_client, client, app, monkeypatch):
    login(client)
    _, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    home = client.get("/api/v1/tmdb-trial/home")
    assert home.status_code == 200
    film = home.json()["cinema_films"][0]
    assert film["release_date"] == "2026-05-01"
    assert film["year"] == 2026

    browse = client.get("/api/v1/tmdb-trial/browse", params={"type": "series", "release_kind": "new", "page": 1})
    assert browse.status_code == 200
    series = browse.json()["items"][0]
    assert series["release_date"] == "2026-04-10"
    assert series["year"] == 2026


def test_home_on_air_uses_air_date_discover(recording_client, client, app, monkeypatch):
    login(client)
    transport, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    response = client.get("/api/v1/tmdb-trial/home")
    assert response.status_code == 200, response.text
    tv_calls = [params for path, params in transport.calls if path == "/discover/tv"]
    assert len(tv_calls) >= 2
    on_air = next(p for p in tv_calls if "air_date.gte" in p)
    assert on_air.get("watch_region") == "FR"
    assert on_air.get("with_watch_monetization_types") == WATCH_MONETIZATION_TYPES_FR
    assert on_air.get("sort_by") == "popularity.desc"
    assert all(path != "/tv/on_the_air" for path, _ in transport.calls)


def test_browse_cache_hits_once_per_identical_request(recording_client, client, app, monkeypatch):
    login(client)
    transport, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    params = {"type": "film", "release_kind": "cinema", "page": 1, "sort": "recent"}
    first = client.get("/api/v1/tmdb-trial/browse", params=params)
    assert first.status_code == 200
    count_after_first = sum(1 for path, _ in transport.calls if path == "/discover/movie")

    second = client.get("/api/v1/tmdb-trial/browse", params=params)
    assert second.status_code == 200
    count_after_second = sum(1 for path, _ in transport.calls if path == "/discover/movie")
    assert count_after_second == count_after_first


def test_endpoints_never_insert_media_rows(recording_client, client, app, monkeypatch):
    login(client)
    _, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    client.get("/api/v1/tmdb-trial/home")
    client.get("/api/v1/tmdb-trial/browse", params={"type": "film", "release_kind": "streaming", "page": 1})

    db = app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(Media)) == 0
    finally:
        db.close()


def test_browse_marks_followed_and_hides_library(recording_client, client, app, monkeypatch):
    login(client)
    _, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)

    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Possédé", year=2099, tmdb_id=1001, followed=True)
        db.add(media)
        db.flush()
        db.add(
            LibraryFile(
                path="/tmp/demo.mkv",
                tmdb_id=1001,
                media_type="film",
                media_id=media.id,
                status="matched",
                size=1,
            )
        )
        db.commit()
    finally:
        db.close()

    listed = client.get(
        "/api/v1/tmdb-trial/browse",
        params={"type": "film", "release_kind": "cinema", "page": 1, "in_library": "false"},
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == []

    all_rows = client.get("/api/v1/tmdb-trial/browse", params={"type": "film", "release_kind": "cinema", "page": 1})
    assert all_rows.json()["items"][0]["followed"] is True
    assert all_rows.json()["items"][0]["in_library"] is True


def test_browse_in_library_false_counts_only_matched_present(recording_client, client, app, monkeypatch):
    """Trashed or missing library rows must not light the catalogue in_library flag."""
    login(client)
    _, mock = recording_client
    monkeypatch.setattr("mediatracker.core.tmdb_trial._client", lambda db, settings: mock)
    monkeypatch.setattr("mediatracker.api.v1.metadata.borrow_client", lambda db, settings: (mock, True))

    db = app.state.session_factory()
    try:
        db.add(
            LibraryFile(
                path="/tmp/matched.mkv",
                tmdb_id=1001,
                media_type="film",
                status="matched",
                size=1,
            )
        )
        db.add(
            LibraryFile(
                path="/tmp/trashed.mkv",
                tmdb_id=1001,
                media_type="film",
                status="trashed",
                size=1,
            )
        )
        db.add(
            LibraryFile(
                path="/tmp/missing.mkv",
                tmdb_id=1001,
                media_type="film",
                status="matched",
                missing_at=NOW,
                size=1,
            )
        )
        db.commit()
    finally:
        db.close()

    browse = client.get(
        "/api/v1/tmdb-trial/browse",
        params={"type": "film", "release_kind": "cinema", "page": 1},
    )
    assert browse.status_code == 200
    assert browse.json()["items"][0]["in_library"] is True

    db = app.state.session_factory()
    try:
        row = db.scalars(select(LibraryFile).where(LibraryFile.path == "/tmp/matched.mkv")).one()
        row.status = "trashed"
        db.commit()
    finally:
        db.close()

    clear_tmdb_trial_cache()
    browse_after = client.get(
        "/api/v1/tmdb-trial/browse",
        params={"type": "film", "release_kind": "cinema", "page": 1},
    )
    assert browse_after.json()["items"][0]["in_library"] is False

    not_in_lib = client.get(
        "/api/v1/tmdb-trial/browse",
        params={"type": "film", "release_kind": "cinema", "page": 1, "in_library": "false"},
    )
    assert not_in_lib.status_code == 200
    assert not_in_lib.json()["items"][0]["tmdb_id"] == 1001

    search = client.get("/api/v1/metadata/tmdb/search", params={"q": "alpha", "type": "film"})
    assert search.status_code == 200
    hit = next(item for item in search.json()["items"] if item["tmdb_id"] == 1001)
    assert hit["in_library"] is False
