"""Parcours API ciblés relecture PR #165 (mutations M2–M11)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from tests.conftest import csrf, login
from tests.test_jellyfin_align import _configure, _db, run_due_jellyfin_push

from mediatracker.db.models import JellyfinLibraryItem, JellyfinPushJob, Media, MediaItem
from mediatracker.integrations.jellyfin_history_import import HistoryLine, apply_import, preview_import
from mediatracker.integrations.tmdb import TmdbHit, TmdbTitle

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def test_parcours_insert_followed_applies_policy_on_existing(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Serie-policy",
            "year": 2020,
            "tmdb_id": 88100,
            "new_episode_policy": "propose",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201
    mid = created.json()["id"]
    db = client.app.state.session_factory()
    row = db.get(Media, mid)
    row.followed = False
    row.auto_download = False
    row.new_episode_policy = "propose"
    db.commit()
    db.close()
    before = len(client.get("/api/v1/media").json()["items"])
    follow = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Serie-policy",
            "tmdb_id": 88100,
            "new_episode_policy": "auto",
            "start_now": True,
        },
        headers=csrf(client),
    )
    assert follow.status_code == 201, follow.text
    assert follow.json()["id"] == mid
    fiche = client.get(f"/api/v1/media/{mid}").json()
    assert fiche["followed"] is True
    assert fiche["new_episode_policy"] == "auto"
    assert fiche["auto_download"] is True
    assert len(client.get("/api/v1/media").json()["items"]) == before


def test_parcours_jellyfin_push_no_job_without_target(client, app, monkeypatch):
    login(client)
    _configure(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Serie-no-target", "year": 2020, "new_episode_policy": "propose"},
        headers=csrf(client),
    )
    mid = created.json()["id"]
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88101}, headers=csrf(client))
    db = _db(app)
    assert db.query(JellyfinPushJob).count() == 0
    db.close()


def test_parcours_jellyfin_push_series_folder_path(client, app, monkeypatch, tmp_path):
    login(client)
    _configure(client)
    series_dir = tmp_path / "Serie-path"
    season = series_dir / "Season 01"
    season.mkdir(parents=True)
    episode = season / "ep-parcours.mkv"
    episode.write_bytes(b"x")
    mid = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Serie-path", "year": 2020, "new_episode_policy": "propose"},
        headers=csrf(client),
    ).json()["id"]
    db = _db(app)
    db.add(
        MediaItem(media_id=mid, season=1, episode=1, title="E1", file_path=str(episode), state="present")
    )
    db.add(
        JellyfinLibraryItem(
            jellyfin_id="jf-series-folder",
            item_type="Series",
            name="Autre-titre",
            year=2020,
            tmdb_id=None,
            local_path=str(series_dir),
            synced_at=NOW,
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88102}, headers=csrf(client))
    calls: list = []
    run_due_jellyfin_push(app, now=NOW, calls=calls)
    db = _db(app)
    job = db.query(JellyfinPushJob).one()
    assert job.state == "done"
    assert any("/RemoteSearch/Apply/jf-series-folder" in c[1] for c in calls)
    db.close()


class FakeSearchImport:
    search_calls = 0

    def __init__(self, token, *a, **k):
        pass

    def search(self, q, kind):
        FakeSearchImport.search_calls += 1
        return [
            TmdbHit(tmdb_id=88110, media_type=kind, title=q, year=2020, overview=None, poster_url=None),
        ]

    def find_external(self, ext, source):
        return None, None

    def fetch(self, tmdb_id, kind):
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type=kind,
            title="Serie-search",
            original_title=None,
            year=2020,
            overview=None,
            poster_url="https://example.test/s.jpg",
            backdrop_url=None,
            series_status="returning",
            release_date=None,
            seasons=(),
        )

    def close(self):
        pass


def test_parcours_import_title_search_and_cache(client, app, monkeypatch):
    del client
    FakeSearchImport.search_calls = 0
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (FakeSearchImport(token), True),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    lines = [
        HistoryLine(2, "movie", "Film-cache-a", 2020, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
        HistoryLine(3, "movie", "Film-cache-a", 2021, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
    ]
    db = app.state.session_factory()
    apply_import(db, app.state.settings, app.state.settings.secret_key, lines, now=NOW)
    db.commit()
    assert FakeSearchImport.search_calls == 1
    rows = db.query(Media).order_by(Media.year).all()
    assert len(rows) == 2
    assert {row.year for row in rows} == {2020, 2021}
    db.close()


def test_parcours_import_episode_homonym_uses_series_year(client, app, monkeypatch):
    del client

    class HomonymSearch:
        def __init__(self, token, *a, **k):
            pass

        def search(self, q, kind):
            return [
                TmdbHit(tmdb_id=5010, media_type=kind, title=q, year=2010, overview=None, poster_url=None),
                TmdbHit(tmdb_id=5020, media_type=kind, title=q, year=2020, overview=None, poster_url=None),
            ]

        def find_external(self, ext, source):
            return None, None

        def fetch(self, tmdb_id, kind):
            return TmdbTitle(
                tmdb_id=tmdb_id,
                media_type=kind,
                title="Reboot-revue",
                original_title=None,
                year=2010,
                overview=None,
                poster_url=None,
                backdrop_url=None,
                series_status="returning",
                release_date=None,
                seasons=(),
            )

        def close(self):
            pass

    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (HomonymSearch(token), True),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    line = HistoryLine(
        2,
        "episode",
        "E1",
        2020,
        "Reboot-revue",
        1,
        1,
        None,
        None,
        None,
        None,
        NOW,
        1,
        "jellyfin",
        series_year=2010,
    )
    db = app.state.session_factory()
    apply_import(db, app.state.settings, app.state.settings.secret_key, [line], now=NOW)
    db.commit()
    row = db.query(Media).one()
    assert row.tmdb_id == 5010
    db.close()


def test_parcours_import_identify_pending_stored(client, app, monkeypatch):
    login(client)

    class MultiSearch:
        def __init__(self, token, *a, **k):
            pass

        def search(self, q, kind):
            return [
                TmdbHit(tmdb_id=88120, media_type=kind, title=q, year=2010, overview=None, poster_url=None),
                TmdbHit(tmdb_id=88121, media_type=kind, title=q, year=2020, overview=None, poster_url=None),
            ]

        def find_external(self, ext, source):
            return None, None

        def close(self):
            pass

    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (MultiSearch(token), True),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    line = HistoryLine(2, "movie", "Film-identify", 2015, None, None, None, None, None, None, None, NOW, 1, "jellyfin")
    db = app.state.session_factory()
    preview = preview_import(
        db,
        [line],
        settings=app.state.settings,
        secret_key=app.state.settings.secret_key,
    )
    assert preview.identify_pending_count >= 1
    apply_import(db, app.state.settings, app.state.settings.secret_key, [line], now=NOW)
    db.commit()
    row = db.query(Media).one()
    assert row.tmdb_id is None
    assert row.tmdb_identify_status == "pending"
    assert row.tmdb_identify_candidates
    listed = client.get("/api/v1/media", params={"identify_status": "pending"}).json()["items"]
    assert any(item["id"] == row.id for item in listed)
    db.close()


def test_parcours_film_availability_keeps_null_source(client, app):
    del client
    from mediatracker.core.film_availability import apply_film_from_tmdb
    from mediatracker.integrations.tmdb import TmdbTitle

    db = app.state.session_factory()
    media = Media(
        type="film",
        title="Film-null-src",
        followed=True,
        tmdb_id=88140,
        next_air_date=date(2026, 12, 1),
        film_availability_source=None,
    )
    db.add(media)
    db.commit()
    fetched = TmdbTitle(
        tmdb_id=88140,
        media_type="film",
        title="Film-null-src",
        year=2024,
        overview=None,
        poster_url=None,
        series_status=None,
        release_date=date(2027, 1, 1),
        seasons=(),
        release_dates=None,
    )
    apply_film_from_tmdb(db, media, fetched, fallback_months=4)
    db.commit()
    assert media.next_air_date == date(2026, 12, 1)
    assert media.film_availability_source is None
    db.close()


def test_parcours_catch_up_slots_honors_force():
    from mediatracker.core.source_poll import expected_slots

    media = Media(type="film", title="Film-force", on_hold=True, next_air_date=date(2099, 1, 1))
    items = [MediaItem(state="missing", watched=False)]
    today = date(2026, 1, 1)
    assert expected_slots(media, items, today=today, window_days=3, force=False, catch_up=True) == []
    forced = expected_slots(media, items, today=today, window_days=3, force=True, catch_up=True)
    assert len(forced) == 1
