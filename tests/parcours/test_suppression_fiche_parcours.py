"""Parcours API : suppression de fiche (suivie ou non)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.db.models import Media, MediaItem, TrashEntry


def _create_film(client, *, followed: bool):
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film-exemple", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    if not followed:
        unfollow = client.post(
            f"/api/v1/media/{media_id}/unfollow",
            json={"trash": False},
            headers=csrf(client),
        )
        assert unfollow.status_code == 200, unfollow.text
    return media_id


def _create_series(client, *, followed: bool):
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Serie-exemple", "year": 2020, "new_episode_policy": "propose"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    if not followed:
        unfollow = client.post(
            f"/api/v1/media/{media_id}/unfollow",
            json={"trash": False},
            headers=csrf(client),
        )
        assert unfollow.status_code == 200, unfollow.text
    return media_id


def _attach_film_file(app, media_id: int) -> Path:
    films = Path(app.state.settings.data_dir) / "media" / "Films"
    path = films / "Film-exemple (2020)" / "Film-exemple (2020).mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"parcours-delete")
    db = app.state.session_factory()
    db.add(
        MediaItem(
            media_id=media_id,
            title="Film-exemple",
            state="downloaded",
            downloaded=True,
            file_path=str(path.resolve()),
        )
    )
    db.commit()
    db.close()
    return path


def _attach_series_file(app, media_id: int) -> Path:
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    path = series / "Serie-exemple" / "Season 01" / "Serie-exemple - S01E01.mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"serie-delete")
    db = app.state.session_factory()
    db.add(
        MediaItem(
            media_id=media_id,
            season=1,
            episode=1,
            title="E1-exemple",
            state="downloaded",
            downloaded=True,
            file_path=str(path.resolve()),
        )
    )
    db.commit()
    db.close()
    return path


def test_parcours_delete_unfollowed_fiche_trashes_files(client, app):
    login(client)
    media_id = _create_film(client, followed=False)
    path = _attach_film_file(app, media_id)
    assert client.get(f"/api/v1/media/{media_id}").json()["followed"] is False

    removed = client.delete(f"/api/v1/media/{media_id}?trash=true", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    assert client.get(f"/api/v1/media/{media_id}").status_code == 404
    assert not path.exists()

    db = app.state.session_factory()
    try:
        entry = db.query(TrashEntry).one()
        assert Path(entry.trash_path).read_bytes() == b"parcours-delete"
        assert db.get(Media, media_id) is None
    finally:
        db.close()


def test_parcours_delete_followed_fiche_keeps_files(client, app):
    login(client)
    media_id = _create_film(client, followed=True)
    path = _attach_film_file(app, media_id)
    assert client.get(f"/api/v1/media/{media_id}").json()["followed"] is True

    removed = client.delete(f"/api/v1/media/{media_id}?trash=false", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    assert client.get(f"/api/v1/media/{media_id}").status_code == 404
    assert path.is_file()
    assert path.read_bytes() == b"parcours-delete"

    db = app.state.session_factory()
    try:
        assert db.query(TrashEntry).count() == 0
        assert db.get(Media, media_id) is None
    finally:
        db.close()


def test_parcours_delete_unfollowed_series_trashes_files(client, app):
    login(client)
    media_id = _create_series(client, followed=False)
    path = _attach_series_file(app, media_id)
    assert client.get(f"/api/v1/media/{media_id}").json()["followed"] is False

    removed = client.delete(f"/api/v1/media/{media_id}?trash=true", headers=csrf(client))
    assert removed.status_code == 204, removed.text
    assert client.get(f"/api/v1/media/{media_id}").status_code == 404
    assert not path.exists()

    db = app.state.session_factory()
    try:
        entry = db.query(TrashEntry).one()
        assert Path(entry.trash_path).read_bytes() == b"serie-delete"
    finally:
        db.close()
