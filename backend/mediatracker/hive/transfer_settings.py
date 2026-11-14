"""Réglages locaux des transferts Hive (T5)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

DEFAULT_PARALLEL = 2
DEFAULT_WEEKLY_QUOTA = 100 * 1024 * 1024 * 1024
DEFAULT_SEND_BPS = 0
DEFAULT_RECV_BPS = 0


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


def window_bounds_minutes(db: Session) -> tuple[int, int]:
    start = _read_int(db, "hive_transfer_window_start", 0)
    end = _read_int(db, "hive_transfer_window_end", 24 * 60)
    start = max(0, min(start, 24 * 60 - 1))
    end = max(1, min(end, 24 * 60))
    if end <= start:
        end = 24 * 60
    return start, end


def in_transfer_window(db: Session, now: datetime) -> bool:
    moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    minutes = moment.hour * 60 + moment.minute
    start, end = window_bounds_minutes(db)
    return start <= minutes < end


def week_start_date(now: datetime) -> date:
    moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    day = moment.date()
    return day.fromordinal(day.toordinal() - day.weekday())
