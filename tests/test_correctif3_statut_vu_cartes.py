"""Correctif 3 — statut « Vu » (champs watched / on_disk) pour films et séries."""

from tests.conftest import csrf, login


def _film(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Titre factice", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"title": "Titre factice"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    return media_id, item.json()["id"]


def _listed_row(client, media_id: int) -> dict:
    listed = client.get("/api/v1/media").json()["items"]
    return next(item for item in listed if item["id"] == media_id)


def _matches_disk_filter(row: dict) -> bool:
    return bool(row.get("on_disk"))


def _matches_waiting_filter(row: dict) -> bool:
    return row.get("availability") == "waiting" and not row.get("watched")


def test_watched_film_without_file_is_vu_not_waiting(client):
    media_id, item_id = _film(client)
    marked = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert marked.status_code == 200, marked.text
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["watched"] is True
    assert detail["on_disk"] is False
    assert detail["availability"] == "waiting"
    assert detail["auto_status"] == "Vu."
    row = _listed_row(client, media_id)
    assert row["watched"] is True
    assert not _matches_waiting_filter(row)
    assert not _matches_disk_filter(row)


def test_watched_film_with_file_is_vu_on_disk_and_in_disk_filter(client, app):
    from pathlib import Path

    media_id, item_id = _film(client)
    movies = Path(app.state.settings.data_dir) / "media" / "Movies"
    dest = movies / "Titre factice (2024)" / "Titre factice (2024).mkv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"video")
    patched = client.patch(
        f"/api/v1/items/{item_id}",
        json={"downloaded": True, "file_path": str(dest.resolve()), "watched": True},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["watched"] is True
    assert detail["on_disk"] is True
    assert detail["availability"] == "on_disk"
    assert detail["auto_status"] == "Vu."
    row = _listed_row(client, media_id)
    assert _matches_disk_filter(row)
    assert not _matches_waiting_filter(row)


def test_unwatched_film_without_source_is_waiting(client):
    media_id, _item_id = _film(client)
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["availability"] == "waiting"
    assert detail["watched"] is False
    assert detail["on_disk"] is False
    row = _listed_row(client, media_id)
    assert _matches_waiting_filter(row)


def test_unwatched_then_watched_then_unwatched_availability(client):
    media_id, item_id = _film(client)
    assert client.get(f"/api/v1/media/{media_id}").json()["availability"] == "waiting"
    assert (
        client.post(
            f"/api/v1/media/{media_id}/watch",
            json={"item_id": item_id},
            headers=csrf(client),
        ).status_code
        == 200
    )
    watched = client.get(f"/api/v1/media/{media_id}").json()
    assert watched["watched"] is True
    assert watched["availability"] == "waiting"
    assert (
        client.post(
            f"/api/v1/media/{media_id}/watch",
            json={"item_id": item_id, "undo": True},
            headers=csrf(client),
        ).status_code
        == 200
    )
    back = client.get(f"/api/v1/media/{media_id}").json()
    assert back["watched"] is False
    assert back["availability"] == "waiting"


def _series(client, title: str = "Série factice"):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": title, "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _episode(client, media_id: int, *, season: int, episode: int, air_date: str, watched: bool = False):
    body = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": season, "episode": episode, "title": f"S{season:02d}E{episode:02d}", "air_date": air_date},
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


def test_series_all_aired_watched_with_future_season_is_vu(client):
    media_id = _series(client)
    _episode(client, media_id, season=1, episode=1, air_date="2026-01-15", watched=True)
    _episode(client, media_id, season=2, episode=1, air_date="2027-06-01", watched=False)
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["watched"] is True
    assert detail["auto_status"] == "Vu."
    row = _listed_row(client, media_id)
    assert row["watched"] is True
    assert not _matches_waiting_filter(row)


def test_series_with_aired_unwatched_episode_is_not_vu(client):
    media_id = _series(client)
    _episode(client, media_id, season=1, episode=1, air_date="2026-01-15", watched=True)
    _episode(client, media_id, season=1, episode=2, air_date="2026-06-01", watched=False)
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["watched"] is False
    assert detail["auto_status"] != "Vu."
    row = _listed_row(client, media_id)
    assert not row["watched"]


def test_series_without_aired_episode_is_never_vu(client):
    media_id = _series(client)
    _episode(client, media_id, season=2, episode=1, air_date="2027-01-01", watched=True)
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["watched"] is False


def test_series_only_season_zero_special_is_not_vu(client):
    media_id = _series(client)
    _episode(client, media_id, season=0, episode=1, air_date="2026-01-01", watched=True)
    detail = client.get(f"/api/v1/media/{media_id}").json()
    assert detail["watched"] is False
