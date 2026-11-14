"""Parcours relecture 2 — mutations M9, N2, N8, N9, N11 et cas Jellyfin V9/V10."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import csrf, login
from tests.test_jellyfin_align import _configure, _db, run_due_jellyfin_push
from tests.test_jellyfin_engine_acceptance import USER, _efcore_db

from mediatracker.db.models import JellyfinLibraryItem, JellyfinPushJob, Media, MediaItem
from mediatracker.integrations.jellyfin_db_export import export_history_bundle
from mediatracker.integrations.jellyfin_history_import import HistoryLine, apply_import, preview_import
from mediatracker.integrations.tmdb import TmdbHit, TmdbTitle

NOW = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
TYPE_MOVIE = "MediaBrowser.Controller.Entities.Movies.Movie"


def test_parcours_jellyfin_push_skips_other_work_tmdb(client, app, monkeypatch):
    login(client)
    _configure(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Homonyme-revue", "year": 2020, "new_episode_policy": "propose"},
        headers=csrf(client),
    )
    mid = created.json()["id"]
    db = _db(app)
    db.add(MediaItem(media_id=mid, title="Local", file_path="/data/revue2/local.mkv"))
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-autre-oeuvre",
            item_type="Series",
            name="Homonyme-revue",
            year=2020,
            tmdb_id=5555,
            synced_at=NOW,
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88106}, headers=csrf(client))
    calls: list = []
    run_due_jellyfin_push(app, now=NOW, calls=calls)
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "skipped"
    assert "introuvable" in (job.last_error or "").casefold()
    assert not any("jf-autre-oeuvre" in c[1] for c in calls)
    db.close()


def test_parcours_jellyfin_push_flat_folder_not_neighbor_series(client, app, monkeypatch, tmp_path):
    login(client)
    _configure(client)
    flat = tmp_path / "plat"
    flat.mkdir()
    episode = flat / "ep-plat.mkv"
    episode.write_bytes(b"x")
    mid = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Serie-plat", "year": 2020, "new_episode_policy": "propose"},
        headers=csrf(client),
    ).json()["id"]
    db = _db(app)
    db.add(MediaItem(media_id=mid, season=1, episode=1, title="E1", file_path=str(episode), state="present"))
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-voisine",
            item_type="Series",
            name="Autre-serie",
            year=2020,
            tmdb_id=None,
            local_path=str(flat / "Autre-serie"),
            synced_at=NOW,
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88107}, headers=csrf(client))
    calls: list = []
    run_due_jellyfin_push(app, now=NOW, calls=calls)
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "skipped"
    assert not any("jf-voisine" in c[1] for c in calls)
    db.close()


def test_parcours_jellyfin_folder_match_ambiguous(client, app, monkeypatch, tmp_path):
    login(client)
    _configure(client)
    root = tmp_path / "show-root"
    root.mkdir()
    ep = root / "s01e01.mkv"
    ep.write_bytes(b"x")
    mid = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Serie-ambig", "year": 2020, "new_episode_policy": "propose"},
        headers=csrf(client),
    ).json()["id"]
    db = _db(app)
    db.add(MediaItem(media_id=mid, season=1, episode=1, title="E1", file_path=str(ep), state="present"))
    same = str(root)
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-a",
            item_type="Series",
            name="A",
            year=2020,
            tmdb_id=None,
            local_path=same,
            synced_at=NOW,
        )
    )
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-b",
            item_type="Series",
            name="B",
            year=2020,
            tmdb_id=None,
            local_path=same,
            synced_at=NOW,
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88108}, headers=csrf(client))
    run_due_jellyfin_push(app, now=NOW, calls=[])
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "skipped"
    assert "ambigu" in (job.last_error or "").casefold()
    db.close()


def test_parcours_engine_movie_without_ids_is_probable(tmp_path):
    db = tmp_path / "j.db"
    _efcore_db(
        db,
        items=f"""
        INSERT INTO BaseItems VALUES ('{USER}', '{TYPE_MOVIE}', 'Film-prob-parcours', 2020,
            NULL, NULL, NULL, NULL, 1, NULL);
        """,
        userdata=f"""
        INSERT INTO UserData (ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate)
        VALUES ('{USER}', '{USER}', '{USER.lower()}', 1, 1, '2024-01-01T12:00:00.0000000Z');
        """,
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    movies = [r for r in bundle.rows if r.type == "movie" and r.title == "Film-prob-parcours"]
    assert len(movies) == 1
    assert movies[0].confiance == "probable"


class FakeFilmSearch:
    search_calls = 0

    def __init__(self, token, *a, **k):
        pass

    def search(self, q, kind):
        FakeFilmSearch.search_calls += 1
        return [TmdbHit(tmdb_id=88150, media_type=kind, title=q, year=2020, overview=None, poster_url=None)]

    def find_external(self, ext, source):
        return None, None

    def fetch(self, tmdb_id, kind):
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type=kind,
            title="Film-cache",
            original_title=None,
            year=2020,
            overview=None,
            poster_url=None,
            backdrop_url=None,
            series_status=None,
            release_date=None,
            seasons=(),
        )

    def close(self):
        pass


def test_parcours_import_search_cache_two_films(client, app, monkeypatch):
    del client
    FakeFilmSearch.search_calls = 0
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (FakeFilmSearch(token), True),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    lines = [
        HistoryLine(2, "movie", "Film-cache", 2020, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
        HistoryLine(3, "movie", "Film-cache", 2021, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
    ]
    db = app.state.session_factory()
    apply_import(db, app.state.settings, app.state.settings.secret_key, lines, now=NOW)
    db.commit()
    assert FakeFilmSearch.search_calls == 1
    assert db.query(Media).count() == 2
    db.close()


def test_parcours_preview_identify_counts_distinct_media(client, app, monkeypatch):
    login(client)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    db = app.state.session_factory()
    db.add(Media(type="film", title="Film-deja", year=2019, followed=False, tmdb_id=88000))
    db.commit()
    db.close()
    lines = [
        HistoryLine(2, "episode", "E1", 2020, "Serie-compte", 1, 1, None, None, None, None, NOW, 1, "jellyfin"),
        HistoryLine(3, "episode", "E2", 2020, "Serie-compte", 1, 2, None, None, None, None, NOW, 1, "jellyfin"),
        HistoryLine(4, "episode", "E3", 2020, "Serie-compte", 1, 3, None, None, None, None, NOW, 1, "jellyfin"),
        HistoryLine(5, "movie", "Film-deja", 2019, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
    ]
    db = app.state.session_factory()
    preview = preview_import(
        db,
        lines,
        settings=app.state.settings,
        secret_key=app.state.settings.secret_key,
    )
    assert preview.identify_pending_count == 1
    db.close()


def test_parcours_align_push_max_attempts(client, app, monkeypatch, tmp_path):
    login(client)
    _configure(client)
    mid = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Serie-max", "year": 2020, "tmdb_id": 88160, "new_episode_policy": "propose"},
        headers=csrf(client),
    ).json()["id"]
    ep = tmp_path / "serie-max.mkv"
    ep.write_bytes(b"x")
    db = _db(app)
    db.add(MediaItem(media_id=mid, season=1, episode=1, title="E1", file_path=str(ep), state="present"))
    db.commit()
    db.close()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    client.patch(
        f"/api/v1/media/{mid}",
        json={"tmdb_id": 88161, "tmdb_id_confirm": "keep_episodes"},
        headers=csrf(client),
    )
    report = client.get("/api/v1/library/jellyfin").json()
    pushes = report.get("pushes") or []
    assert pushes
    assert pushes[0].get("max_attempts") == 5
