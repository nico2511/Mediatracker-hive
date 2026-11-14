"""Weekly SQLite VACUUM and ANALYZE during a quiet hour when the library is idle."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.metrics.worker_counters import TASK_CLASSIFY, TASK_LIBRARY_SCAN, is_task_active
from mediatracker.core.verify_all import verify_all_running
from mediatracker.core.worker_schedule import has_active_downloads
from mediatracker.db.models import Setting
from mediatracker.maintenance.backup import sqlite_path

logger = logging.getLogger(__name__)

LAST_AT_KEY = "sqlite.maintenance_at"
_INTERVAL = timedelta(days=7)


def maybe_maintain_sqlite(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> bool:
    """Run VACUUM and ANALYZE when due. Returns True when maintenance ran."""
    moment = _aware(now or datetime.now(timezone.utc))
    if not _weekly_due(db, moment):
        return False
    if not _in_quiet_hour(db, settings, moment):
        return False
    if _library_busy(db):
        return False
    path = sqlite_path(settings.database_url)
    if path is None:
        return False
    try:
        _vacuum_analyze(path)
    except OSError:
        logger.info("Maintenance SQLite (VACUUM/ANALYZE) impossible.")
        return False
    _touch_last(db, moment)
    logger.info("Maintenance SQLite terminée (VACUUM et ANALYZE).")
    return True


def _library_busy(db: Session) -> bool:
    if has_active_downloads(db):
        return True
    if verify_all_running():
        return True
    return is_task_active(TASK_LIBRARY_SCAN, TASK_CLASSIFY)


def _weekly_due(db: Session, moment: datetime) -> bool:
    last = _parse_stamp(db)
    if last is None:
        return True
    return moment - last >= _INTERVAL


def _in_quiet_hour(db: Session, settings: Settings, moment: datetime) -> bool:
    hour = settings.sqlite_maintenance_hour
    if hour < 0 or hour > 23:
        hour = 4
    tz_name = _timezone(db, settings)
    try:
        local = moment.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        local = moment.astimezone(timezone.utc)
    return local.hour == hour


def _timezone(db: Session, settings: Settings) -> str:
    row = db.get(Setting, "notify_timezone")
    if row is not None and row.value:
        return row.value
    return settings.notify_timezone


def _vacuum_analyze(path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=120.0)
    try:
        connection.execute("VACUUM")
        connection.execute("ANALYZE")
        connection.commit()
    finally:
        connection.close()


def _parse_stamp(db: Session) -> datetime | None:
    row = db.get(Setting, LAST_AT_KEY)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return _aware(parsed)


def _touch_last(db: Session, moment: datetime) -> None:
    row = db.get(Setting, LAST_AT_KEY)
    text = moment.isoformat()
    if row is None:
        db.add(Setting(key=LAST_AT_KEY, value=text, is_secret=False, updated_at=moment))
    else:
        row.value = text
        row.updated_at = moment
    db.flush()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
