"""Weekly SQLite maintenance during the quiet hour."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from mediatracker.db.models import Setting
from mediatracker.maintenance.sqlite_tune import LAST_AT_KEY, maybe_maintain_sqlite


def _utc_settings(client):
    return client.app.state.settings.model_copy(
        update={"sqlite_maintenance_hour": 4, "notify_timezone": "UTC"}
    )


def _use_utc_timezone(db, moment: datetime) -> None:
    row = db.get(Setting, "notify_timezone")
    assert row is not None
    row.value = "UTC"
    row.updated_at = moment
    db.commit()


def test_sqlite_maintenance_skips_active_downloads(client):
    settings = _utc_settings(client)
    moment = datetime(2026, 3, 2, 4, 30, tzinfo=timezone.utc)
    db = client.app.state.session_factory()
    try:
        _use_utc_timezone(db, moment)
        with patch("mediatracker.maintenance.sqlite_tune.has_active_downloads", return_value=True):
            assert maybe_maintain_sqlite(db, settings, now=moment) is False
    finally:
        db.close()


def test_sqlite_maintenance_runs_when_due(client):
    settings = _utc_settings(client)
    moment = datetime(2026, 3, 2, 4, 15, tzinfo=timezone.utc)
    db = client.app.state.session_factory()
    try:
        _use_utc_timezone(db, moment)
        with (
            patch("mediatracker.maintenance.sqlite_tune._vacuum_analyze") as vacuum,
            patch("mediatracker.maintenance.sqlite_tune.has_active_downloads", return_value=False),
            patch("mediatracker.maintenance.sqlite_tune.verify_all_running", return_value=False),
            patch("mediatracker.maintenance.sqlite_tune.is_task_active", return_value=False),
        ):
            assert maybe_maintain_sqlite(db, settings, now=moment) is True
        vacuum.assert_called_once()
        row = db.get(Setting, LAST_AT_KEY)
        assert row is not None
        assert row.value == moment.isoformat()
    finally:
        db.close()


def test_sqlite_maintenance_waits_for_quiet_hour(client):
    settings = _utc_settings(client)
    moment = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    db = client.app.state.session_factory()
    try:
        _use_utc_timezone(db, moment)
        with patch("mediatracker.maintenance.sqlite_tune._vacuum_analyze") as vacuum:
            assert maybe_maintain_sqlite(db, settings, now=moment) is False
        vacuum.assert_not_called()
    finally:
        db.close()


def test_sqlite_maintenance_respects_weekly_interval(client):
    settings = _utc_settings(client)
    moment = datetime(2026, 3, 2, 4, 0, tzinfo=timezone.utc)
    recent = moment - timedelta(days=2)
    db = client.app.state.session_factory()
    try:
        _use_utc_timezone(db, moment)
        db.add(Setting(key=LAST_AT_KEY, value=recent.isoformat(), is_secret=False, updated_at=recent))
        db.commit()
        with patch("mediatracker.maintenance.sqlite_tune._vacuum_analyze") as vacuum:
            assert maybe_maintain_sqlite(db, settings, now=moment) is False
        vacuum.assert_not_called()
    finally:
        db.close()
