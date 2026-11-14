"""Sorties numériques on the Home Assistant summary, without calling TMDB from the GET."""

from datetime import datetime, timedelta, timezone

import httpx
from tests.conftest import csrf, login

from mediatracker.core.digital_releases import maybe_refresh_digital_releases, refresh_digital_releases
from mediatracker.db.models import Media
from mediatracker.integrations.tmdb import TmdbClient


def _bearer(client) -> dict[str, str]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": "ha-releases", "scopes": ["read"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def _client() -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/discover/tv":
            params = dict(request.url.params)
            assert params["watch_region"] == "FR"
            assert "air_date.gte" in params
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 900101,
                            "name": "Chimie Démo (FRENCH)",
                            "overview": "Un professeur.",
                            "poster_path": "/bb.jpg",
                            "first_air_date": "2008-01-20",
                        }
                    ]
                },
            )
        assert request.url.path == "/3/discover/movie"
        params = dict(request.url.params)
        assert params["region"] == "FR"
        assert params["with_release_type"] == "4"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 901002,
                        "title": "Club Démo",
                        "poster_path": "/club-demo.jpg",
                        "release_date": "2026-10-20",
                    },
                    {
                        "id": 900005,
                        "title": "Archipel Pilote",
                        "poster_path": "/archipel-demo.jpg",
                        "release_date": "2026-10-13",
                    },
                    {"id": 99, "title": "", "release_date": "2026-10-01"},
                ]
            },
        )

    return TmdbClient("tmdb-test-token", transport=httpx.MockTransport(handler))


def test_summary_and_releases_list_cached_digital_titles(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        assert refresh_digital_releases(db, app.state.settings, now=now, client=_client()) is True
        media = db.get(Media, created.json()["id"])
        media.tmdb_id = 900005
        media.followed = True
        db.commit()
    finally:
        db.close()

    headers = _bearer(client)
    client.cookies.clear()
    summary = client.get("/api/v1/ha/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    rows = summary.json()["digital_releases"]
    assert [row["title"] for row in rows] == ["Archipel Pilote", "Club Démo"]
    archipel = rows[0]
    assert archipel["date"] == "2026-10-13"
    assert archipel["type"] == "movie"
    assert archipel["tmdb_id"] == 900005
    assert archipel["tracked"] is True
    assert archipel["poster_url"] == "https://image.tmdb.org/t/p/w500/archipel-demo.jpg"
    assert rows[1]["tracked"] is False
    assert rows[1]["poster_url"].endswith("/club-demo.jpg")
    assert rows[0]["overview"] is None
    airing = summary.json()["airing_series"]
    assert [row["title"] for row in airing] == ["Chimie Démo"]
    assert airing[0]["type"] == "tv"
    assert airing[0]["tmdb_id"] == 900101
    assert airing[0]["overview"] == "Un professeur."
    assert airing[0]["poster_url"].endswith("/bb.jpg")

    listed = client.get("/api/v1/ha/releases", headers=headers)
    assert listed.status_code == 200, listed.text
    movies = [row for row in listed.json()["items"] if row["type"] == "movie"]
    assert movies == rows

    db = app.state.session_factory()
    try:
        assert (
            maybe_refresh_digital_releases(
                db,
                app.state.settings,
                now=now + timedelta(hours=1),
                client=_client(),
            )
            is False
        )
    finally:
        db.close()


def test_releases_require_a_token(client):
    assert client.get("/api/v1/ha/releases").status_code == 401
