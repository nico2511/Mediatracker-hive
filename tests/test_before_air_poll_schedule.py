"""Report next_source_poll_at to release day when the gate is before_air_date."""

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from mediatracker.core.source_poll import next_due, run_poll
from mediatracker.core.worker_schedule import _future_deadlines
from mediatracker.db.models import Media, MediaItem

NOW = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
RELEASE = date(2026, 11, 17)


def _release_utc(settings) -> datetime:
    zone = ZoneInfo(settings.notify_timezone)
    start = datetime.combine(RELEASE, time.min, tzinfo=zone)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


class _SilentSource:
    def search(self, *_args, **_kwargs):
        raise AssertionError("no network before release")

    def get_links(self, _ref):
        raise AssertionError("no network before release")

    def close(self) -> None:
        return None


def _film(db, *, poll_at: datetime | None) -> Media:
    media = Media(
        type="film",
        title="Exemple Avant Sortie",
        followed=True,
        on_hold=False,
        new_episode_policy="auto",
        next_air_date=RELEASE,
        next_source_poll_at=poll_at,
        last_source_poll_at=NOW.replace(tzinfo=None) - timedelta(days=1),
        created_at=NOW.replace(tzinfo=None),
    )
    db.add(media)
    db.flush()
    db.add(MediaItem(media_id=media.id, title="Exemple Avant Sortie", air_date=date(2026, 11, 1)))
    db.flush()
    return media


def _series(db, *, poll_at: datetime | None) -> Media:
    media = Media(
        type="series",
        title="Exemple Saison Future",
        followed=True,
        on_hold=False,
        series_status="returning",
        new_episode_policy="propose",
        next_air_date=RELEASE,
        next_episode_season=1,
        next_episode_number=3,
        next_source_poll_at=poll_at,
        created_at=NOW.replace(tzinfo=None),
    )
    db.add(media)
    db.flush()
    db.add(
        MediaItem(
            media_id=media.id,
            season=1,
            episode=3,
            title="Épisode futur",
            air_date=RELEASE,
        )
    )
    db.flush()
    return media


def test_next_due_defers_film_to_release_day(client, app):
    del client
    db = app.state.session_factory()
    try:
        past = (NOW - timedelta(hours=2)).replace(tzinfo=None)
        media = _film(db, poll_at=past)
        db.commit()
        assert next_due(db, app.state.settings, NOW) is None
        db.refresh(media)
        assert media.next_source_poll_at == _release_utc(app.state.settings)
    finally:
        db.close()


def test_run_poll_skipped_series_reports_release(client, app):
    del client
    db = app.state.session_factory()
    try:
        past = (NOW - timedelta(minutes=30)).replace(tzinfo=None)
        media = _series(db, poll_at=past)
        db.commit()
        report = run_poll(
            db,
            media,
            1,
            _SilentSource(),
            app.state.settings,
            now=NOW,
            rng=random.Random(0),
            force=False,
        )
        db.commit()
        expected = _release_utc(app.state.settings)
        assert report.result == "skipped"
        assert report.next_check_at == expected.replace(tzinfo=timezone.utc)
        assert media.next_source_poll_at == expected
    finally:
        db.close()


def test_poll_allowed_after_release_day(client, app):
    del client
    after = datetime(2026, 11, 17, 14, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        media = _film(db, poll_at=(after - timedelta(hours=1)).replace(tzinfo=None))
        db.commit()
        chosen = next_due(db, app.state.settings, after)
        assert chosen is not None
        assert chosen.id == media.id
    finally:
        db.close()


def test_forced_poll_does_not_reschedule_before_release(client, app):
    del client
    db = app.state.session_factory()
    try:
        past = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        media = _film(db, poll_at=past)
        db.commit()

        class _Source:
            calls = 0

            def search(self, *_a, **_k):
                _Source.calls += 1
                return []

            def get_links(self, _ref):
                return []

            def close(self) -> None:
                return None

        report = run_poll(
            db,
            media,
            1,
            _Source(),
            app.state.settings,
            now=NOW,
            rng=random.Random(1),
            force=True,
            schedule_finish=False,
        )
        assert report.result == "nothing"
        assert _Source.calls == 1
        assert media.next_source_poll_at == past
    finally:
        db.close()


def test_worker_schedule_uses_deferred_release(client, app):
    del client
    db = app.state.session_factory()
    try:
        past = (NOW - timedelta(minutes=5)).replace(tzinfo=None)
        _film(db, poll_at=past)
        db.commit()
        assert next_due(db, app.state.settings, NOW) is None
        expected = _release_utc(app.state.settings)
        poll_min = db.scalar(
            select(func.min(Media.next_source_poll_at)).where(
                Media.followed.is_(True),
                Media.on_hold.is_(False),
                Media.next_source_poll_at.is_not(None),
                Media.next_source_poll_at > NOW.replace(tzinfo=None),
            )
        )
        assert poll_min == expected
        futures = _future_deadlines(db, app.state.settings, NOW)
        assert expected.replace(tzinfo=timezone.utc) in futures
    finally:
        db.close()
