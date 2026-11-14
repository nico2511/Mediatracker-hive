"""Parcours « À voir » : non vu + fichier présent, avec ou sans suivi."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import csrf, login
from tests.test_ha_summary import _bearer

from mediatracker.core.film_follow import stop_following_downloaded_film
from mediatracker.core.force_download import IN_PROGRESS
from mediatracker.core.series_follow import stop_following_complete_series
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)


def _to_watch_titles(client) -> set[str]:
    board = client.get("/api/v1/dashboard").json()
    return {group["media_title"] for group in board["to_watch_groups"]}


def _ha_to_watch(client) -> dict[str, int]:
    headers = _bearer(client, ["read"], "parcours-a-voir")
    client.cookies.clear()
    body = client.get("/api/v1/ha/summary", headers=headers).json()
    return body["to_watch"]


def _add_present_episode(db, media_id: int, *, season: int, episode: int, title: str, path: str) -> MediaItem:
    item = MediaItem(
        media_id=media_id,
        season=season,
        episode=episode,
        title=title,
        downloaded=True,
        watched=False,
        downloaded_at=NOW,
        file_path=path,
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=path,
            media_type="series",
            status="matched",
            media_item_id=item.id,
            seen_at=NOW,
        )
    )
    return item


def test_parcours_a_voir_film_arret_auto(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Exemple Film Auto", "year": 2023},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        item = MediaItem(
            media_id=created["id"],
            title="Exemple Film Auto",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
            file_path="/data/exemple-film-auto.mkv",
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path="/data/exemple-film-auto.mkv",
                media_type="film",
                status="matched",
                media_item_id=item.id,
                seen_at=NOW,
            )
        )
        media = db.get(Media, created["id"])
        assert media is not None
        assert stop_following_downloaded_film(db, media, reason="downloaded")
        db.commit()
    finally:
        db.close()

    assert "Exemple Film Auto" in _to_watch_titles(client)
    counts = _ha_to_watch(client)
    assert counts["films"] == 1
    assert counts["episodes"] == 0


def test_parcours_a_voir_serie_terminee_arret_auto(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Serie Auto", "year": 2023},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        media = db.get(Media, created["id"])
        assert media is not None
        media.followed = True
        media.series_status = "ended"
        _add_present_episode(
            db,
            created["id"],
            season=1,
            episode=1,
            title="Pilote",
            path="/data/exemple-serie-auto.mkv",
        )
        assert stop_following_complete_series(db, media)
        db.commit()
    finally:
        db.close()

    assert "Exemple Serie Auto" in _to_watch_titles(client)


def test_parcours_a_voir_arret_manuel(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Arret Manuel", "year": 2024},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        _add_present_episode(
            db,
            created["id"],
            season=1,
            episode=2,
            title="Suite",
            path="/data/exemple-arret-manuel.mkv",
        )
        db.commit()
    finally:
        db.close()

    unfollow = client.post(
        f"/api/v1/media/{created['id']}/unfollow",
        json={"trash": False},
        headers=csrf(client),
    )
    assert unfollow.status_code == 200, unfollow.text
    assert "Exemple Arret Manuel" in _to_watch_titles(client)


def test_parcours_a_voir_exclut_vu_absent_corbeille_telechargement(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple Exclusions", "year": 2025},
        headers=csrf(client),
    ).json()
    db = app.state.session_factory()
    try:
        watched = MediaItem(
            media_id=created["id"],
            season=1,
            episode=1,
            title="Vu",
            downloaded=True,
            watched=True,
            downloaded_at=NOW,
            file_path="/data/excl-vu.mkv",
        )
        absent = MediaItem(
            media_id=created["id"],
            season=1,
            episode=2,
            title="Absent",
            downloaded=False,
            watched=False,
            state="missing",
        )
        trash_item = MediaItem(
            media_id=created["id"],
            season=1,
            episode=3,
            title="Corbeille",
            downloaded=True,
            watched=False,
            downloaded_at=NOW,
            file_path="/data/excl-trash.mkv",
        )
        dl_item = MediaItem(
            media_id=created["id"],
            season=1,
            episode=4,
            title="En cours",
            downloaded=False,
            watched=False,
            state="downloading",
        )
        db.add_all([watched, absent, trash_item, dl_item])
        db.flush()
        db.add_all(
            [
                LibraryFile(
                    path="/data/excl-vu.mkv",
                    media_type="series",
                    status="matched",
                    media_item_id=watched.id,
                    seen_at=NOW,
                ),
                LibraryFile(
                    path="/data/excl-trash.mkv",
                    media_type="series",
                    status="trashed",
                    media_item_id=trash_item.id,
                    seen_at=NOW,
                ),
            ]
        )
        db.add(
            Download(
                media_item_id=dl_item.id,
                state=IN_PROGRESS[0],
                progress=10,
                speed=0,
                size=100,
                created_at=NOW,
            )
        )
        db.commit()
    finally:
        db.close()

    titles = _to_watch_titles(client)
    assert "Exemple Exclusions" not in titles
