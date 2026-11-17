"""W-statut : statut TMDB à jour et fin du suivi des séries terminées."""

from datetime import date, datetime, timezone

from alembic import command
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mediatracker.core.pipeline.queue import _finish_download
from mediatracker.core.presence import local_video_in
from mediatracker.core.seasons import apply_new_seasons, maybe_refresh_seasons
from mediatracker.core.series_follow import stop_following_complete_series
from mediatracker.core.storage import storage_layout
from mediatracker.db.migrate import make_config
from mediatracker.db.models import Download, Event, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbTitle

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
TODAY = NOW.date()


class _FakeTmdb:
    def __init__(self, title: TmdbTitle) -> None:
        self._title = title

    def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
        return self._title


def _series(
    db,
    *,
    title: str = "Série-Démo",
    series_status: str = "ended",
    followed: bool = True,
    tmdb_id: int = 42,
) -> Media:
    media = Media(
        type="series",
        title=title,
        year=2020,
        tmdb_id=tmdb_id,
        followed=followed,
        series_status=series_status,
        created_at=NOW,
    )
    db.add(media)
    db.flush()
    return media


def _episode(
    db,
    media: Media,
    season: int,
    episode: int,
    *,
    air_date: date | None = None,
    watched: bool = False,
    downloaded: bool = False,
    file_path: str | None = None,
) -> MediaItem:
    item = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"S{season}E{episode}",
        air_date=air_date,
        watched=watched,
        downloaded=downloaded,
        file_path=file_path,
        state="downloaded" if downloaded else "missing",
    )
    db.add(item)
    return item


def test_refresh_updates_series_status(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="returning")
        fetched = TmdbTitle(
            tmdb_id=42,
            media_type="series",
            title="Exemple",
            year=2020,
            overview=None,
            poster_url=None,
            series_status="ended",
            release_date=None,
            seasons=(),
        )
        apply_new_seasons(db, media, fetched, NOW)
        db.commit()
        assert media.series_status == "ended"
        event = db.scalar(
            select(Event).where(Event.type == "media.series_status_changed", Event.media_id == media.id)
        )
        assert event is not None
        assert event.payload["previous"] == "returning"
        assert event.payload["status"] == "ended"
    finally:
        db.close()


def test_refresh_keeps_status_when_tmdb_unknown(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="returning")
        fetched = TmdbTitle(
            tmdb_id=42,
            media_type="series",
            title="Exemple",
            year=2020,
            overview=None,
            poster_url=None,
            series_status="unknown",
            release_date=None,
            seasons=(),
        )
        apply_new_seasons(db, media, fetched, NOW)
        db.commit()
        assert media.series_status == "returning"
        assert (
            db.scalar(
                select(Event).where(Event.type == "media.series_status_changed", Event.media_id == media.id)
            )
            is None
        )
    finally:
        db.close()


def test_ended_complete_series_unfollowed(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        _episode(db, media, 1, 2, air_date=date(2020, 1, 8), watched=True)
        db.commit()
        assert stop_following_complete_series(db, media, today=TODAY)
        db.commit()
        assert media.followed is False
        event = db.scalar(select(Event).where(Event.type == "media.unfollowed", Event.media_id == media.id))
        assert event is not None
        assert event.payload["reason"] == "complete"
        assert event.payload["keep_files"] is True
        assert event.payload["via"] == "watched"
        assert event.payload.get("automatic") is True
    finally:
        db.close()


def test_ended_all_downloaded_unfollowed(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), downloaded=True, file_path="/tmp/a.mkv")
        _episode(db, media, 1, 2, air_date=date(2020, 1, 8), downloaded=True, file_path="/tmp/b.mkv")
        db.commit()
        assert stop_following_complete_series(db, media, today=TODAY)
        db.commit()
        assert media.followed is False
        event = db.scalar(select(Event).where(Event.type == "media.unfollowed", Event.media_id == media.id))
        assert event is not None
        assert event.payload["via"] == "downloaded"
    finally:
        db.close()


def test_manual_refollow_respected_until_change(client, app):
    from mediatracker.core.follow import FollowInput, follow

    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        db.commit()
        assert stop_following_complete_series(db, media, today=TODAY)
        db.commit()
        assert media.followed is False
        stored = media.series_auto_unfollow_key
        assert stored
        follow(db, None, body=FollowInput(type="series", title=media.title), media=media, source="patch")
        db.commit()
        assert media.followed is True
        assert not stop_following_complete_series(db, media, today=TODAY)
        assert media.followed is True
        _episode(db, media, 2, 1, air_date=date(2020, 2, 1))
        db.commit()
        assert not stop_following_complete_series(db, media, today=TODAY)
        ep = db.scalars(
            select(MediaItem).where(MediaItem.media_id == media.id, MediaItem.season == 2, MediaItem.episode == 1)
        ).one()
        ep.watched = True
        db.commit()
        assert stop_following_complete_series(db, media, today=TODAY)
        db.commit()
        assert media.followed is False
        assert media.series_auto_unfollow_key != stored
    finally:
        db.close()


def test_ended_incomplete_series_stays_followed(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        _episode(db, media, 1, 2, air_date=date(2020, 1, 8))
        db.commit()
        assert not stop_following_complete_series(db, media, today=TODAY)
        assert media.followed is True
    finally:
        db.close()


def test_ongoing_complete_series_stays_followed(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="returning")
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        db.commit()
        assert not stop_following_complete_series(db, media, today=TODAY)
        assert media.followed is True
    finally:
        db.close()


def test_season_zero_missing_ignored(client, app):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        _episode(db, media, 0, 1, air_date=date(2019, 1, 1))
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        db.commit()
        assert stop_following_complete_series(db, media, today=TODAY)
        assert media.followed is False
    finally:
        db.close()


def test_maybe_refresh_unfollows_complete_series(client, app, settings):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended", tmdb_id=99)
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        db.commit()
        fetched = TmdbTitle(
            tmdb_id=99,
            media_type="series",
            title="Exemple",
            year=2020,
            overview=None,
            poster_url=None,
            series_status="ended",
            release_date=None,
            seasons=(),
        )
        maybe_refresh_seasons(db, settings, now=NOW, client=_FakeTmdb(fetched))
        db.expire_all()
        row = db.get(Media, media.id)
        assert row is not None
        assert row.followed is False
    finally:
        db.close()


def test_finish_download_unfollows_complete_series(client, app, settings, tmp_path):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        first = _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        second = _episode(db, media, 1, 2, air_date=date(2020, 1, 8))
        db.flush()
        layout = storage_layout(db, settings)
        dest = layout.series_dir / "Exemple" / "Season 01" / "e02.mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 64)
        download = Download(media_item_id=second.id, state="running", created_at=NOW)
        db.add(download)
        db.commit()
        _finish_download(db, download, second, media, dest, dest, NOW, settings, store_release=False)
        db.commit()
        assert media.followed is False
        assert first.watched is True
    finally:
        db.close()


def _media_item_at_0040(
    session: Session,
    media_id: int,
    season: int,
    episode: int,
    *,
    title: str = "E1",
    air_date: date | None = None,
    watched: bool = False,
    state: str = "missing",
) -> None:
    from sqlalchemy import text

    session.execute(
        text(
            "INSERT INTO media_item (media_id, season, episode, title, air_date, watched, state, "
            "downloaded, source_available, keep, traces) "
            "VALUES (:media_id, :season, :episode, :title, :air_date, :watched, :state, 0, 0, 0, '[]')"
        ),
        {
            "media_id": media_id,
            "season": season,
            "episode": episode,
            "title": title,
            "air_date": air_date,
            "watched": watched,
            "state": state,
        },
    )


def _seed_media_at_0040(session: Session, **kwargs) -> Media:
    from sqlalchemy import text

    title = kwargs.get("title", "Exemple")
    tmdb_id = kwargs.get("tmdb_id", 7)
    series_status = kwargs.get("series_status", "ended")
    row_id = session.execute(
        text(
            "INSERT INTO media (type, title, year, tmdb_id, followed, follow_origin, auto_download, "
            "pref_quality, pref_language, allow_fallback, keep, series_status, on_hold, "
            "catch_up_pending, new_episode_policy, download_backlog, source_error_streak, "
            "source_error_epoch, created_at) "
            "VALUES ('series', :title, 2020, :tmdb_id, 1, 'user', 0, 'HD', 'VFF', 0, 0, "
            ":series_status, 0, 0, 'propose', 0, 0, 0, :created_at)"
        ),
        {"title": title, "tmdb_id": tmdb_id, "series_status": series_status, "created_at": NOW},
    ).lastrowid
    return Media(id=row_id, type="series", title=title, year=2020, tmdb_id=tmdb_id, followed=True)


def test_migration_0041_sql_backfill(tmp_path):
    url = f"sqlite:///{tmp_path / 'm0041.db'}"
    config = make_config(url)
    command.upgrade(config, "0040_watch_manual_at")
    engine = create_engine(url)
    session = Session(engine)
    try:
        complete = _seed_media_at_0040(session, tmdb_id=1, series_status="canceled")
        _media_item_at_0040(
            session,
            complete.id,
            1,
            1,
            air_date=date(2019, 5, 1),
            watched=True,
            state="watched",
        )
        incomplete = _seed_media_at_0040(session, tmdb_id=2, title="Incomplète", series_status="ended")
        _media_item_at_0040(
            session,
            incomplete.id,
            1,
            1,
            air_date=date(2019, 5, 1),
            watched=True,
            state="watched",
        )
        _media_item_at_0040(
            session,
            incomplete.id,
            1,
            2,
            title="E2",
            air_date=date(2019, 6, 1),
            state="missing",
        )
        ongoing = _seed_media_at_0040(session, tmdb_id=3, title="En cours", series_status="returning")
        _media_item_at_0040(
            session,
            ongoing.id,
            1,
            1,
            air_date=date(2019, 5, 1),
            watched=True,
            state="watched",
        )
        session.commit()
        complete_id = complete.id
        incomplete_id = incomplete.id
        ongoing_id = ongoing.id
    finally:
        session.close()
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(url)
    session = Session(engine)
    try:
        assert session.get(Media, complete_id).followed is False
        assert session.get(Media, incomplete_id).followed is True
        assert session.get(Media, ongoing_id).followed is True
    finally:
        session.close()
        engine.dispose()


def test_presence_unfollows_complete_series(client, app, settings, tmp_path):
    db = app.state.session_factory()
    try:
        media = _series(db, series_status="ended")
        _episode(db, media, 1, 1, air_date=date(2020, 1, 1), watched=True)
        last = _episode(db, media, 1, 2, air_date=date(2020, 1, 8))
        db.commit()
        layout = storage_layout(db, settings)
        path = layout.series_dir / "Exemple" / "Season 01" / "e02.mkv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"y" * 64)
        last.file_path = str(path)
        db.commit()
        found = local_video_in(db, layout, last, NOW)
        assert found == path
        db.commit()
        assert media.followed is False
    finally:
        db.close()
