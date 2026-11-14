"""W-vu: vu/non vu complet et fin du suivi des films téléchargés."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.pipeline.queue import _finish_download
from mediatracker.core.presence import local_video_in
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import Download, Event, Media, MediaItem
from mediatracker.integrations.jellyfin import JellyfinWatch
from mediatracker.integrations.jellyfin_sync import apply_played

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
USER = "11111111-1111-1111-1111-111111111111"


def _configure_jellyfin_push(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.test",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": USER,
                "jellyfin_push_watched": True,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def test_series_all_watched_and_unwatched(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    for ep in (1, 2):
        client.post(
            f"/api/v1/media/{media_id}/items",
            json={"season": 1, "episode": ep, "title": f"E{ep}"},
            headers=csrf(client),
        )
    marked = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"all": True},
        headers=csrf(client),
    )
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["updated"] == 2
    assert "marqués vus" in body["message"]
    undone = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"all": True, "undo": True},
        headers=csrf(client),
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["updated"] == 2
    assert undone.json()["message"] == "2 épisode(s) marqués non vus."


def test_jellyfin_watched_can_be_cleared_manually(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple", year=2024, tmdb_id=1, created_at=NOW)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=date(2026, 1, 1),
            state="watched",
            watched=True,
            watch_source="auto",
            jellyfin_item_id="jf-1",
        )
        db.add(item)
        db.commit()
        media_id = media.id
        item_id = item.id
    finally:
        db.close()
    cleared = client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"item_id": item_id, "undo": True},
        headers=csrf(client),
    )
    assert cleared.status_code == 200, cleared.text
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        assert row.watched is False
        assert row.watch_source == "manual"
        assert row.state == "missing"
    finally:
        db.close()


def test_manual_unwatched_survives_jellyfin_played_without_date(client, app):
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple", year=2024, tmdb_id=2, created_at=NOW)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=date(2026, 1, 1),
            state="missing",
            watched=False,
            watch_source="manual",
            watch_manual_at=NOW,
        )
        db.add(item)
        db.commit()
        watch = JellyfinWatch(
            item_id="jf",
            item_type="Episode",
            tmdb_id=2,
            season=1,
            episode=1,
            path=None,
            played=True,
        )
        apply_played(db, item, watch, NOW)
        db.commit()
        assert item.watched is False
        assert item.watch_source == "manual"
    finally:
        db.close()


def test_manual_unwatched_yields_to_newer_jellyfin_playback(client, app):
    manual_at = NOW - timedelta(hours=2)
    jellyfin_at = NOW - timedelta(minutes=5)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple", year=2024, tmdb_id=3, created_at=NOW)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=date(2026, 1, 1),
            state="missing",
            watched=False,
            watch_source="manual",
            watch_manual_at=manual_at,
        )
        db.add(item)
        db.commit()
        watch = JellyfinWatch(
            item_id="jf",
            item_type="Episode",
            tmdb_id=3,
            season=1,
            episode=1,
            path=None,
            played=True,
            last_played_at=jellyfin_at,
        )
        apply_played(db, item, watch, NOW)
        db.commit()
        assert item.watched is True
        assert item.watch_source == "auto"
        assert item.watch_manual_at is None
    finally:
        db.close()


def test_manual_unwatched_keeps_older_jellyfin_playback(client, app):
    manual_at = NOW
    jellyfin_at = NOW - timedelta(days=1)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple", year=2024, tmdb_id=4, created_at=NOW)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=date(2026, 1, 1),
            state="missing",
            watched=False,
            watch_source="manual",
            watch_manual_at=manual_at,
        )
        db.add(item)
        db.commit()
        watch = JellyfinWatch(
            item_id="jf",
            item_type="Episode",
            tmdb_id=4,
            season=1,
            episode=1,
            path=None,
            played=True,
            last_played_at=jellyfin_at,
        )
        apply_played(db, item, watch, NOW)
        db.commit()
        assert item.watched is False
        assert item.watch_source == "manual"
    finally:
        db.close()


def test_manual_unwatched_then_webhook_playback_marks_watched(client, app):
    login(client)
    from mediatracker.core.watch_manual_lock import stamp_manual_choice

    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024, "tmdb_id": 5},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Un"},
        headers=csrf(client),
    )
    item_id = episode.json()["id"]
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        row.watched = True
        row.watch_source = "manual"
        stamp_manual_choice(row, NOW - timedelta(hours=1))
        row.watched = False
        row.watched_at = None
        stamp_manual_choice(row, NOW - timedelta(minutes=30))
        db.commit()
    finally:
        db.close()
    from tests.test_jellyfin import _configure

    _configure(client)
    done = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=webhook-secret-value",
        json={
            "NotificationType": "PlaybackStop",
            "ItemType": "Episode",
            "ItemId": "jf-wh",
            "Provider_tmdb": "5",
            "SeasonNumber": 1,
            "EpisodeNumber": 1,
            "PlayedToCompletion": True,
            "UserId": "user-1",
        },
    )
    assert done.status_code == 200, done.text
    assert done.json()["updated"] == 1
    body = client.get(f"/api/v1/media/{media_id}/items").json()
    assert body["seasons"][0]["episodes"][0]["watched"] is True


def test_push_played_and_unplayed_to_jellyfin(client, app, monkeypatch):
    login(client)
    _configure_jellyfin_push(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Un"},
        headers=csrf(client),
    )
    item_id = episode.json()["id"]
    calls: list[tuple[str, str]] = []

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def close(self) -> None:
            return None

        def mark_played(self, user_id: str, jellyfin_id: str) -> None:
            calls.append(("played", jellyfin_id))

        def mark_unplayed(self, user_id: str, jellyfin_id: str) -> None:
            calls.append(("unplayed", jellyfin_id))

    monkeypatch.setattr("mediatracker.integrations.jellyfin.JellyfinClient", _Client)
    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_sync.resolve_configured_user",
        lambda _db, _client, _configured, _moment=None: USER,
    )
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        row.jellyfin_item_id = "jf-ep"
        db.commit()
    finally:
        db.close()
    client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    client.post(
        f"/api/v1/media/{media_id}/watch",
        json={"item_id": item_id, "undo": True},
        headers=csrf(client),
    )
    assert calls == [("played", "jf-ep"), ("unplayed", "jf-ep")]


def test_film_unfollow_after_download(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"title": "Film"},
        headers=csrf(client),
    )
    item_id = item.json()["id"]
    movies = Path(app.state.settings.data_dir) / "media" / "Movies"
    dest = movies / "Exemple (2024)" / "Exemple (2024).mkv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"video")
    source = Path(app.state.settings.data_dir) / "downloads" / "incomplete" / "dl.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        row = db.get(MediaItem, item_id)
        download = Download(media_item_id=item_id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()
        _finish_download(db, download, row, media, dest, source, NOW, store_release=False)
        db.commit()
        db.refresh(media)
        assert media.followed is False
        events = list(db.scalars(select(Event).where(Event.type == "media.unfollowed", Event.media_id == media_id)))
        assert len(events) == 1
        assert events[0].payload.get("reason") == "downloaded"
        assert events[0].payload.get("keep_files") is True
    finally:
        db.close()


def test_series_stays_followed_after_download(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Un"},
        headers=csrf(client),
    )
    item_id = episode.json()["id"]
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    dest = series / "Exemple (2024)" / "Season 01" / "Exemple - S01E01.mkv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"video")
    source = Path(app.state.settings.data_dir) / "downloads" / "incomplete" / "dl.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        row = db.get(MediaItem, item_id)
        download = Download(media_item_id=item_id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()
        _finish_download(db, download, row, media, dest, source, NOW, store_release=False)
        db.commit()
        db.refresh(media)
        assert media.followed is True
    finally:
        db.close()


def test_migration_unfollows_downloaded_films(tmp_path):
    from alembic import command

    from mediatracker.db.migrate import make_config

    url = f"sqlite:///{tmp_path / 'wvu.db'}"
    cfg = make_config(url)
    command.upgrade(cfg, "0038_user_session_epoch")
    engine = __import__("sqlalchemy").create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO media (type, title, year, followed, created_at) VALUES ('film', 'A', 2020, 1, '2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO media (type, title, year, followed, created_at) VALUES ('series', 'B', 2020, 1, '2026-01-01')"
        )
        film_id = conn.exec_driver_sql("SELECT id FROM media WHERE type = 'film'").scalar()
        series_id = conn.exec_driver_sql("SELECT id FROM media WHERE type = 'series'").scalar()
        conn.exec_driver_sql(
            f"INSERT INTO media_item (media_id, state, downloaded, file_path) "
            f"VALUES ({film_id}, 'downloaded', 1, '/films/a.mkv')"
        )
        conn.exec_driver_sql(
            f"INSERT INTO media_item (media_id, season, episode, state, downloaded, file_path) "
            f"VALUES ({series_id}, 1, 1, 'downloaded', 1, '/series/a.mkv')"
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        film = conn.exec_driver_sql("SELECT followed FROM media WHERE type = 'film'").scalar()
        series = conn.exec_driver_sql("SELECT followed FROM media WHERE type = 'series'").scalar()
    assert film == 0
    assert series == 1


def test_presence_unfollows_downloaded_film(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Exemple", year=2024, followed=True, created_at=NOW)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing", downloaded=False)
        db.add(item)
        db.commit()
        movies = Path(app.state.settings.data_dir) / "media" / "Movies"
        video = movies / "Exemple (2024).mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"v")
        item.file_path = str(video)
        layout = storage_layout(db, app.state.settings)
        local_video_in(db, layout, item, NOW)
        db.commit()
        db.refresh(media)
        assert media.followed is False
    finally:
        db.close()
