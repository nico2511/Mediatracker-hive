"""Parcours API (TestClient) pour les correctifs A–D et folders/follow."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import csrf, login
from tests.test_jellyfin_align import _configure, _db, run_due_jellyfin_push

from mediatracker.db.models import IdentityChange, JellyfinLibraryItem, JellyfinPushJob, Media, MediaItem, MediaSeason
from mediatracker.integrations.jellyfin_history_import import HistoryLine, apply_import
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbSeason, TmdbTitle

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class FakeTmdbPatch:
    calls: list[tuple] = []

    def __init__(self, token, *a, **k):
        pass

    def fetch(self, tmdb_id, media_type):
        FakeTmdbPatch.calls.append(("fetch", tmdb_id, media_type))
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title="Serie-parcours",
            original_title="Serie-parcours-orig",
            year=2021,
            overview="Synopsis-parcours",
            poster_url="https://example.test/poster.jpg",
            backdrop_url="https://example.test/back.jpg",
            series_status="returning",
            release_date=None,
            seasons=(
                TmdbSeason(
                    number=1,
                    name="Saison 1",
                    overview=None,
                    air_date=None,
                    episode_count=1,
                    tmdb_id=9001,
                    episodes=(
                        TmdbEpisode(
                            season=1,
                            episode=1,
                            title="E1-parcours",
                            air_date=None,
                            tmdb_episode_id=91001,
                        ),
                    ),
                ),
            ),
        )

    def close(self):
        pass


def _unfollowed(client, *, tmdb_id=None):
    body = {"type": "series", "title": "Fiche-parcours", "year": 2020, "new_episode_policy": "propose"}
    if tmdb_id is not None:
        body["tmdb_id"] = tmdb_id
    created = client.post("/api/v1/media", json=body, headers=csrf(client))
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    db = client.app.state.session_factory()
    db.get(Media, media_id).followed = False
    db.commit()
    db.close()
    return media_id


def test_parcours_a_patch_tmdb_refreshes_metadata(client, monkeypatch):
    login(client)
    mid = _unfollowed(client)
    FakeTmdbPatch.calls.clear()
    monkeypatch.setattr("mediatracker.api.v1.media.TmdbClient", FakeTmdbPatch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    r = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88001}, headers=csrf(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tmdb_id"] == 88001
    assert body["title"] == "Serie-parcours"
    assert body["overview"] == "Synopsis-parcours"
    assert body["poster_url"] == "https://example.test/poster.jpg"
    assert FakeTmdbPatch.calls == [("fetch", 88001, "series")]
    db = client.app.state.session_factory()
    change = db.query(IdentityChange).filter(IdentityChange.media_id == mid).one()
    assert change.new_title == "Serie-parcours"
    db.close()


def test_parcours_a_patch_tmdb_merges_watched_episodes(client, monkeypatch):
    login(client)
    mid = _unfollowed(client)
    db = client.app.state.session_factory()
    db.add(MediaSeason(media_id=mid, number=1, name="S1", episode_count=1))
    watched = MediaItem(
        media_id=mid,
        season=1,
        episode=1,
        title="E1-local",
        state="watched",
        watched=True,
        file_path="/data/parcours/s01e01.mkv",
    )
    db.add(watched)
    db.commit()
    watched_id = watched.id
    db.close()
    FakeTmdbPatch.calls.clear()
    monkeypatch.setattr("mediatracker.api.v1.media.TmdbClient", FakeTmdbPatch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    r = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88001}, headers=csrf(client))
    assert r.status_code == 200, r.text
    db = client.app.state.session_factory()
    items = db.query(MediaItem).filter(MediaItem.media_id == mid).all()
    assert len(items) == 1
    row = db.get(MediaItem, watched_id)
    assert row is not None
    assert row.watched is True
    assert row.file_path == "/data/parcours/s01e01.mkv"
    assert row.title == "E1-parcours"
    db.close()


class FakeTmdbFilmPatch(FakeTmdbPatch):
    def fetch(self, tmdb_id, media_type):
        FakeTmdbPatch.calls.append(("fetch", tmdb_id, media_type))
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title="Film-parcours-tmdb",
            original_title=None,
            year=2022,
            overview="Film-overview",
            poster_url="https://example.test/film.jpg",
            backdrop_url=None,
            series_status=None,
            release_date=None,
            seasons=(),
        )


def test_parcours_a_patch_tmdb_merges_film_item(client, monkeypatch):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film-parcours", "year": 2022, "new_episode_policy": "propose"},
        headers=csrf(client),
    )
    assert created.status_code == 201
    mid = created.json()["id"]
    db = client.app.state.session_factory()
    db.get(Media, mid).followed = False
    db.add(MediaItem(media_id=mid, title="Film-parcours", state="watched", watched=True))
    db.commit()
    db.close()
    FakeTmdbPatch.calls.clear()
    monkeypatch.setattr("mediatracker.api.v1.media.TmdbClient", FakeTmdbFilmPatch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    r = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88021}, headers=csrf(client))
    assert r.status_code == 200, r.text
    db = client.app.state.session_factory()
    items = db.query(MediaItem).filter(MediaItem.media_id == mid).all()
    assert len(items) == 1
    assert items[0].watched is True
    assert items[0].title == "Film-parcours-tmdb"
    db.close()


def test_parcours_a_patch_tmdb_without_token_returns_warning(client, monkeypatch):
    login(client)
    mid = _unfollowed(client)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    r = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88001}, headers=csrf(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tmdb_id"] == 88001
    assert body.get("tmdb_refresh_warning")


def test_parcours_b_follow_existing_without_duplicate(client):
    login(client)
    mid = _unfollowed(client)
    before = client.get("/api/v1/media").json()["items"]
    r = client.post(
        f"/api/v1/media/{mid}/follow",
        json={"start_now": False, "new_episode_policy": "auto"},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == mid
    fiche = client.get(f"/api/v1/media/{mid}").json()
    assert fiche["followed"] is True
    assert fiche["new_episode_policy"] == "auto"
    assert fiche["auto_download"] is True
    after = client.get("/api/v1/media").json()["items"]
    assert len(after) == len(before)


def _attach_local_file(db, media_id: int) -> None:
    item = MediaItem(media_id=media_id, title="Local", file_path="/data/parcours/local.mkv")
    db.add(item)
    db.commit()


def test_parcours_c_jellyfin_push_skipped_without_library_row(client, app, monkeypatch):
    login(client)
    _configure(client)
    mid = _unfollowed(client)
    db = _db(app)
    _attach_local_file(db, mid)
    monkeypatch.setattr("mediatracker.api.v1.media.TmdbClient", FakeTmdbPatch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88002}, headers=csrf(client))
    calls: list = []
    run_due_jellyfin_push(app, now=NOW, calls=calls)
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "skipped"
    assert job.attempts == 1
    assert "introuvable" in (job.last_error or "").casefold()
    db.close()


def test_parcours_c_jellyfin_push_matches_title_and_completes(client, app, monkeypatch):
    login(client)
    _configure(client)
    mid = _unfollowed(client)
    db = _db(app)
    _attach_local_file(db, mid)
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-parcours-1",
            item_type="Series",
            name="Fiche-parcours",
            year=2020,
            tmdb_id=None,
            synced_at=NOW,
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.api.v1.media.TmdbClient", FakeTmdbPatch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88003}, headers=csrf(client))
    calls: list = []
    run_due_jellyfin_push(app, now=NOW, calls=calls)
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "done"
    assert any("/RemoteSearch/Apply/" in c[1] for c in calls)
    db.close()


class FakeTmdbImport:
    calls: list = []

    def __init__(self, token, *a, **k):
        pass

    def search(self, q, kind):
        FakeTmdbImport.calls.append(("search", q, kind))
        return []

    def find_external(self, ext, source):
        FakeTmdbImport.calls.append(("find", source, ext))
        return 88010, "series"

    def fetch(self, tmdb_id, kind):
        FakeTmdbImport.calls.append(("fetch", tmdb_id, kind))
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type=kind,
            title="Serie-import",
            original_title=None,
            year=2020,
            overview="Import-overview",
            poster_url="https://example.test/import.jpg",
            backdrop_url=None,
            series_status="returning",
            release_date=None,
            seasons=(),
        )

    def close(self):
        pass


def test_parcours_d_jellyfin_import_resolves_tvdb(client, app, monkeypatch):
    del client
    FakeTmdbImport.calls.clear()
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (FakeTmdbImport(token), True),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    line = HistoryLine(
        3,
        "episode",
        "E1",
        2020,
        "Serie-tvdb",
        1,
        1,
        None,
        None,
        54321,
        None,
        NOW,
        1,
        "jellyfin",
    )
    db = app.state.session_factory()
    apply_import(db, app.state.settings, app.state.settings.secret_key, [line], now=NOW)
    db.commit()
    row = db.query(Media).one()
    assert row.tmdb_id == 88010
    assert row.poster_url == "https://example.test/import.jpg"
    assert ("find", "tvdb_id", "54321") in FakeTmdbImport.calls
    db.close()


def test_parcours_c_jellyfin_push_ambiguous_title_skipped(client, app, monkeypatch):
    login(client)
    _configure(client)
    mid = _unfollowed(client)
    db = _db(app)
    _attach_local_file(db, mid)
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-homonym-a",
            item_type="Series",
            name="Serie-parcours",
            year=None,
            tmdb_id=None,
            synced_at=NOW,
        )
    )
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-homonym-b",
            item_type="Series",
            name="Serie-parcours",
            year=None,
            tmdb_id=None,
            synced_at=NOW,
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.api.v1.media.TmdbClient", FakeTmdbPatch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88004}, headers=csrf(client))
    run_due_jellyfin_push(app, now=NOW, calls=[])
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "skipped"
    assert "ambigu" in (job.last_error or "").casefold()
    db.close()


def test_parcours_library_folder_follow_existing_fiche(client, app, settings, tmp_path, monkeypatch):
    """Non-régression : suivre un dossier rattache la fiche existante (même titre) sans en créer une autre."""
    from mediatracker.core.library_plan import FolderRow

    login(client)
    movies = tmp_path / "movies"
    movies.mkdir()
    folder = movies / "Film-parcours (2022)"
    folder.mkdir()
    db = app.state.session_factory()
    media = Media(type="film", title="Film-parcours", year=2022, followed=False, tmdb_id=None)
    db.add(media)
    db.commit()
    mid = media.id
    db.close()
    row = FolderRow(
        path=str(folder.resolve()),
        name=folder.name,
        media_type="film",
        title="Film-parcours",
        year=2022,
        tmdb_id=88020,
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
        json={"path": str(folder), "follow": True},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["followed"] is True
    assert body.get("media_id") == mid
    db = app.state.session_factory()
    row = db.get(Media, mid)
    assert row.followed is True
    db.close()
    listed = client.get("/api/v1/media").json()["items"]
    assert sum(1 for item in listed if item["title"] == "Film-parcours") == 1


def test_parcours_library_folder_follow_homonym_other_year_keeps_tmdb(client, app, tmp_path, monkeypatch):
    """V12 : homonyme d'une autre année ne reçoit pas le tmdb_id du dossier (rapprochement titre seul)."""
    from mediatracker.core.library_plan import FolderRow

    login(client)
    movies = tmp_path / "movies"
    movies.mkdir()
    folder = movies / "Homonyme-dossier (2022)"
    folder.mkdir()
    db = app.state.session_factory()
    media = Media(type="film", title="Homonyme-dossier", year=1990, followed=False, tmdb_id=None)
    db.add(media)
    db.commit()
    mid = media.id
    db.close()
    row = FolderRow(
        path=str(folder.resolve()),
        name=folder.name,
        media_type="film",
        title="Homonyme-dossier",
        year=2022,
        tmdb_id=88301,
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
        json={"path": str(folder), "follow": True},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    assert r.json().get("media_id") == mid
    db = app.state.session_factory()
    row = db.get(Media, mid)
    assert row.tmdb_id is None
    assert row.year == 1990
    assert row.followed is True
    db.close()
