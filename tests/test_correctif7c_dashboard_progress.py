"""Correctif 7c — Progression excludes fully watched followed media."""

from tests.conftest import csrf, login


def _series(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série factice A", "year": 2025},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _episode(client, media_id: int, *, season: int, episode: int, air_date: str, watched: bool):
    body = client.post(
        f"/api/v1/media/{media_id}/items",
        json={
            "season": season,
            "episode": episode,
            "title": f"S{season:02d}E{episode:02d}",
            "air_date": air_date,
        },
        headers=csrf(client),
    )
    assert body.status_code == 201, body.text
    item_id = body.json()["id"]
    if watched:
        marked = client.post(
            f"/api/v1/media/{media_id}/watch",
            json={"item_id": item_id},
            headers=csrf(client),
        )
        assert marked.status_code == 200, marked.text
    return item_id


def _film(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film factice B", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"title": "Film factice B"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    return media_id, item.json()["id"]


def _progress_ids(client) -> set[int]:
    board = client.get("/api/v1/dashboard").json()
    return {row["media_id"] for row in board["progress"]}


def test_progress_omits_series_with_all_aired_episodes_watched(client):
    media_id = _series(client)
    _episode(client, media_id, season=1, episode=1, air_date="2026-01-10", watched=True)
    _episode(client, media_id, season=1, episode=2, air_date="2026-02-01", watched=True)
    _episode(client, media_id, season=2, episode=1, air_date="2027-06-01", watched=False)
    assert media_id not in _progress_ids(client)


def test_progress_keeps_series_with_unwatched_aired_episode(client):
    media_id = _series(client)
    _episode(client, media_id, season=1, episode=1, air_date="2026-01-10", watched=True)
    _episode(client, media_id, season=1, episode=2, air_date="2026-03-01", watched=False)
    assert media_id in _progress_ids(client)


def test_progress_omits_watched_film_but_keeps_unwatched(client):
    watched_id, watched_item = _film(client)
    client.post(
        f"/api/v1/media/{watched_id}/watch",
        json={"item_id": watched_item},
        headers=csrf(client),
    )
    unwatched_id, _ = _film(client)
    progress = _progress_ids(client)
    assert watched_id not in progress
    assert unwatched_id in progress
