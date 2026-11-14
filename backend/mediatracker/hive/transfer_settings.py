"""Réglages locaux des transferts Hive (T5)."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

DEFAULT_PARALLEL = 2
DEFAULT_WEEKLY_QUOTA = 100 * 1024 * 1024 * 1024
DEFAULT_SEND_BPS = 0
DEFAULT_RECV_BPS = 0
DEFAULT_LOCAL_TZ = "Europe/Paris"


def _read_int(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or row.value is None or not str(row.value).strip():
        return default
    try:
        return int(str(row.value).strip())
    except ValueError:
        return default


def _read_bool(db: Session, key: str, default: bool) -> bool:
    row = db.get(Setting, key)
    if row is None or row.value is None:
        return default
    return str(row.value).strip().lower() in {"1", "true", "yes", "on"}


def transfer_parallel_limit(db: Session) -> int:
    n = _read_int(db, "hive_transfer_parallel", DEFAULT_PARALLEL)
    return max(1, min(n, 16))


def transfer_send_bps(db: Session) -> int:
    return max(0, _read_int(db, "hive_transfer_send_bps", DEFAULT_SEND_BPS))


def transfer_recv_bps(db: Session) -> int:
    return max(0, _read_int(db, "hive_transfer_recv_bps", DEFAULT_RECV_BPS))


def hive_instance_timezone() -> ZoneInfo:
    """Fuseau de l'instance (variable d'environnement TZ, défaut Europe/Paris)."""
    name = (os.environ.get("TZ") or DEFAULT_LOCAL_TZ).strip() or DEFAULT_LOCAL_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_LOCAL_TZ)


def window_bounds_minutes(db: Session) -> tuple[int, int]:
    start = _read_int(db, "hive_transfer_window_start", 0)
    end = _read_int(db, "hive_transfer_window_end", 24 * 60)
    start = max(0, min(start, 24 * 60 - 1))
    end = max(1, min(end, 24 * 60))
    return start, end


def _minutes_since_midnight_local(now: datetime) -> int:
    moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local = moment.astimezone(hive_instance_timezone())
    return local.hour * 60 + local.minute


def in_transfer_window(db: Session, now: datetime) -> bool:
    """Créneau quotidien en heure locale du conteneur (TZ). Début == fin → toujours ouvert."""
    start, end = window_bounds_minutes(db)
    if start == end:
        return True
    minutes = _minutes_since_midnight_local(now)
    if end > start:
        return start <= minutes < end
    if end < start:
        return minutes >= start or minutes < end
    return False


def week_start_date(now: datetime) -> date:
    moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    day = moment.date()
    return day.fromordinal(day.toordinal() - day.weekday())