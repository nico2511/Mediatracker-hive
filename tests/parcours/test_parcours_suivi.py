"""Parcours « suivre / ne plus suivre » : fige le comportement de chaque entrée API."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from tests.conftest import csrf, login
from tests.parcours.test_parcours_a_voir import _to_watch_titles
from tests.test_ha_summary import _bearer

from mediatracker.core.film_follow import stop_following_downloaded_film
from mediatracker.db.models import Event, LibraryFile, Media, MediaItem, Setting

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)


def _patch_borrow_tmdb(monkeypatch, fn) -> None:
    """``insert_followed`` appelle ``borrow_tmdb`` via ``core.follow`` (PR) ou ``api.v1.media`` (main)."""
    for path in (
        "mediatracker.core.follow.borrow_tmdb",
        "mediatracker.api.v1.media.borrow_tmdb",
    ):
        try:
            monkeypatch.setattr(path, fn)
        except ImportError:
            continue


def _unfollowed_series(client, *, title: str = "Serie-parcours-suivi", tmdb_id: int | None = None) -> int:
    body: dict = {"type": "series", "title": title, "year": 2020, "new_episode_policy": "propose"}
    if tmdb_id is not None:
        body["tmdb_id"] = tmdb_id
    created = client.post("/api/v1/media", json=body, headers=csrf(client))
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    db = client.app.state.session_factory()
    row = db.get(Media, media_id)
    assert row is not None
    row.followed = False
    db.commit()
    db.close()
    return media_id


def test_parcours_suivi_post_media_reactive_politique_et_rattrapage(client):
    """Écran formulaire : POST /media sur fiche existante."""
    login(client)
    mid = _unfollowed_series(client, title="Serie-post-media", tmdb_id=88201)
    follow = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Serie-post-media",
            "tmdb_id": 88201,
            "new_episode_policy": "auto",
            "start_now": True,
            "seasons": [2, 1],
        },
        headers=csrf(client),
    )
    assert follow.status_code == 201, follow.text
    assert follow.json()["id"] == mid
    fiche = client.get(f"/api/v1/media/{mid}").json()
    assert fiche["followed"] is True
    assert fiche["new_episode_policy"] == "auto"
    assert fiche["auto_download"] is True
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    assert row.tracked_seasons == [1, 2]
    assert row.catch_up_pending is True
    assert row.download_backlog is True
    event = db.scalar(select(Event).where(Event.type == "media.followed", Event.media_id == mid))
    assert event is not None
    db.close()


def test_parcours_suivi_media_id_follow(client):
    """Écran liste / aperçu rapide : POST /media/{id}/follow."""
    login(client)
    mid = _unfollowed_series(client)
    before = len(client.get("/api/v1/media").json()["items"])
    r = client.post(
        f"/api/v1/media/{mid}/follow",
        json={"new_episode_policy": "auto", "seasons": [1], "start_now": False},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    fiche = client.get(f"/api/v1/media/{mid}").json()
    assert fiche["followed"] is True
    assert fiche["new_episode_policy"] == "auto"
    assert len(client.get("/api/v1/media").json()["items"]) == before
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    assert row.tracked_seasons == [1]
    assert row.catch_up_pending is True
    assert row.download_backlog is False
    db.close()


def test_parcours_suivi_patch_followed_declenche_rattrapage(client):
    """Fiche détail : PATCH followed=true avec politique auto."""
    login(client)
    mid = _unfollowed_series(client)
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    row.new_episode_policy = "auto"
    row.on_hold = False
    db.commit()
    db.close()
    r = client.patch(f"/api/v1/media/{mid}", json={"followed": True}, headers=csrf(client))
    assert r.status_code == 200, r.text
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    assert row.followed is True
    assert row.catch_up_pending is True
    assert row.next_source_poll_at is not None
    db.close()


def test_parcours_suivi_patch_followed_combine_ui_media_detail(client):
    """Fiche détail : PATCH combiné (followed + politique auto + reprise), comme MediaDetailView."""
    login(client)
    mid = _unfollowed_series(client, title="Serie-patch-combine-ui")
    combined_follow = {
        "followed": True,
        "on_hold": False,
        "new_episode_policy": "auto",
    }
    r = client.patch(f"/api/v1/media/{mid}", json=combined_follow, headers=csrf(client))
    assert r.status_code == 200, r.text
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    assert row is not None
    assert row.followed is True
    assert row.new_episode_policy == "auto"
    assert row.on_hold is False
    db.close()

    combined_unfollow = {
        "followed": False,
        "on_hold": False,
        "new_episode_policy": "auto",
    }
    r2 = client.patch(f"/api/v1/media/{mid}", json=combined_unfollow, headers=csrf(client))
    assert r2.status_code == 200, r2.text
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    assert row is not None
    assert row.followed is False
    db.close()


def test_parcours_suivi_catalogue(client, app):
    """Écran catalogue : POST /catalog/{id}/follow."""
    from tests.test_catalog import NOW as CAT_NOW
    from tests.test_catalog import _Feed, _listing

    from mediatracker.core.catalog import refresh_catalog
    from mediatracker.db.models import CatalogEntry

    feed = _Feed(
        {
            "film": [],
            "series": [
                _listing(
                    title="Orbite-parcours-suivi",
                    year=2022,
                    media_type="series",
                    season=1,
                    url="https://wawa.test/orbite-suivi",
                    ref="https://wawa.test/orbite-suivi",
                ),
            ],
        }
    )
    db = app.state.session_factory()
    refresh_catalog(db, app.state.settings, now=CAT_NOW, sources=[(1, "wawacity", feed)], force=True)
    entry = db.scalar(select(CatalogEntry).where(CatalogEntry.title == "Orbite-parcours-suivi"))
    assert entry is not None
    entry_id = entry.id
    db.close()

    login(client)
    created = client.post(
        f"/api/v1/catalog/{entry_id}/follow",
        json={"new_episode_policy": "propose", "seasons": [1], "start_now": True},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["followed"] is True
    assert body["new_episode_policy"] == "propose"
    db = app.state.session_factory()
    media = db.scalar(select(Media).where(Media.title == "Orbite-parcours-suivi"))
    assert media is not None
    assert media.tracked_seasons == [1]
    assert media.download_backlog is True
    db.close()


def test_parcours_suivi_ha_sans_doublon(client, monkeypatch):
    """Home Assistant : POST /ha/actions/follow."""
    from mediatracker.integrations.tmdb import TmdbTitle

    class _Tmdb:
        def __init__(self, token, **_kwargs) -> None:
            pass

        def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
            return TmdbTitle(
                tmdb_id=tmdb_id,
                media_type=media_type,
                title="Film-ha-parcours",
                year=2020,
                overview=None,
                poster_url=None,
                series_status=None,
                release_date=None,
                seasons=(),
            )

        def close(self) -> None:
            return None

    login(client)

    def _borrow_tmdb(token, factory):
        return _Tmdb(token), True

    _patch_borrow_tmdb(monkeypatch, _borrow_tmdb)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    headers = _bearer(client, ["write"], "parcours-suivi-ha")
    client.cookies.clear()
    created = client.post(
        "/api/v1/ha/actions/follow",
        headers=headers,
        json={"tmdb_id": 88202, "type": "movie", "auto_download": True},
    )
    assert created.status_code == 200, created.text
    assert created.json()["already"] is False
    db = client.app.state.session_factory()
    media = db.scalar(select(Media).where(Media.tmdb_id == 88202, Media.type == "film"))
    assert media is not None
    assert media.followed is True
    assert media.auto_download is True
    db.close()
    again = client.post(
        "/api/v1/ha/actions/follow",
        headers=headers,
        json={"tmdb_id": 88202, "type": "movie"},
    )
    assert again.json()["already"] is True


def test_parcours_suivi_folder_unfollow(client, app, tmp_path, monkeypatch):
    """Bibliothèque : POST /library/folders/follow avec follow=false."""
    from mediatracker.core.library_plan import FolderRow

    login(client)
    movies = tmp_path / "movies"
    movies.mkdir()
    folder = movies / "Film-unfollow (2021)"
    folder.mkdir()
    db = app.state.session_factory()
    media = Media(type="film", title="Film-unfollow", year=2021, followed=True, tmdb_id=None)
    db.add(media)
    db.commit()
    mid = media.id
    db.close()
    row = FolderRow(
        path=str(folder.resolve()),
        name=folder.name,
        media_type="film",
        title="Film-unfollow",
        year=2021,
        tmdb_id=None,
        confidence="high",
        source="folder",
        files=(),
    )
    monkeypatch.setattr(
        "mediatracker.api.v1.library.library_roots",
        lambda db, s: (movies, tmp_path / "series"),
    )
    monkeypatch.setattr(
        "mediatracker.api.v1.library.list_folders",
        lambda *a, **k: [row],
    )
    r = client.post(
        "/api/v1/library/folders/follow",
        json={"path": str(folder), "follow": False},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["followed"] is False
    db = app.state.session_factory()
    assert db.get(Media, mid).followed is False
    events = list(db.scalars(select(Event).where(Event.media_id == mid, Event.type == "media.unfollowed")))
    assert events == []
    db.close()


def test_parcours_suivi_jellyfin_refollow(client, app):
    """Import Jellyfin : POST /library/jellyfin-history/refollow."""
    login(client)
    db = app.state.session_factory()
    media = Media(
        type="film",
        title="Film-refollow-parcours",
        year=2019,
        followed=False,
        follow_origin="jellyfin_import",
        tmdb_id=88203,
    )
    db.add(media)
    db.flush()
    db.add(Setting(key="jellyfin_history.follow_candidates", value=json.dumps([media.id])))
    db.commit()
    mid = media.id
    db.close()
    r = client.post(
        "/api/v1/library/jellyfin-history/refollow",
        json={"media_ids": [mid]},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    db = app.state.session_factory()
    row = db.get(Media, mid)
    assert row.followed is True
    assert row.follow_origin == "jellyfin_import"
    event = db.scalar(select(Event).where(Event.type == "media.followed", Event.media_id == mid))
    assert event is not None
    assert event.payload.get("from") == "jellyfin_import"
    db.close()


def test_parcours_suivi_unfollow_manuel_rest_dans_a_voir(client, app):
    """Arrêt manuel : POST /media/{id}/unfollow conserve « À voir »."""
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film-unfollow-a-voir", "year": 2022},
        headers=csrf(client),
    ).json()
    mid = created["id"]
    db = app.state.session_factory()
    item = MediaItem(
        media_id=mid,
        title="Film-unfollow-a-voir",
        downloaded=True,
        watched=False,
        downloaded_at=NOW,
        file_path="/data/film-unfollow-a-voir.mkv",
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path="/data/film-unfollow-a-voir.mkv",
            media_type="film",
            status="matched",
            media_item_id=item.id,
            seen_at=NOW,
        )
    )
    db.commit()
    db.close()
    unfollow = client.post(f"/api/v1/media/{mid}/unfollow", json={"trash": False}, headers=csrf(client))
    assert unfollow.status_code == 200, unfollow.text
    assert unfollow.json()["followed"] is False
    assert "Film-unfollow-a-voir" in _to_watch_titles(client)


def test_parcours_suivi_auto_film_evenement(client, app):
    """Arrêt auto film : raison dans le journal, fiche toujours « À voir »."""
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film-auto-suivi", "year": 2021},
        headers=csrf(client),
    ).json()
    mid = created["id"]
    db = app.state.session_factory()
    item = MediaItem(
        media_id=mid,
        title="Film-auto-suivi",
        downloaded=True,
        watched=False,
        downloaded_at=NOW,
        file_path="/data/film-auto-suivi.mkv",
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path="/data/film-auto-suivi.mkv",
            media_type="film",
            status="matched",
            media_item_id=item.id,
            seen_at=NOW,
        )
    )
    media = db.get(Media, mid)
    assert stop_following_downloaded_film(db, media, reason="downloaded")
    db.commit()
    event = db.scalar(select(Event).where(Event.type == "media.unfollowed", Event.media_id == mid))
    assert event is not None
    assert event.payload.get("reason") == "downloaded"
    assert event.payload.get("keep_files") is True
    db.close()
    assert "Film-auto-suivi" in _to_watch_titles(client)
