"""Pause the background worker during destructive SQLite maintenance."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.core.worker_wake import wake_worker
from mediatracker.db.models import Setting

MAINTENANCE_KEY = "worker.maintenance"
MAINTENANCE_AT_KEY = "worker.maintenance_at"
ACK_KEY = "worker.maintenance_ack"
ACK_WAIT_SECONDS = 60.0
_ACK_POLL_SECONDS = 0.2


def worker_maintenance_active(db: Session) -> bool:
    row = db.get(Setting, MAINTENANCE_KEY)
    return row is not None and row.value == "1"


def enter_worker_maintenance(db: Session) -> None:
    moment = datetime.now(timezone.utc)
    _clear_ack(db, moment)
    _put(db, MAINTENANCE_KEY, "1", moment)
    _put(db, MAINTENANCE_AT_KEY, moment.isoformat(), moment)
    db.commit()


def acknowledge_worker_maintenance(db: Session) -> None:
    moment = datetime.now(timezone.utc)
    _put(db, ACK_KEY, moment.isoformat(), moment)
    db.flush()


def exit_worker_maintenance(db: Session, settings: Settings) -> None:
    moment = datetime.now(timezone.utc)
    _put(db, MAINTENANCE_KEY, "0", moment)
    _clear_ack(db, moment)
    row = db.get(Setting, MAINTENANCE_AT_KEY)
    if row is not None:
        db.delete(row)
    db.commit()
    wake_worker(db, settings, "maintenance.restore", commit=True)


def wait_for_worker_maintenance_ack(
    session_factory: sessionmaker[Session],
    *,
    timeout: float | None = None,
) -> bool:
    """Return True when the worker has acked after the latest maintenance request."""
    limit = timeout if timeout is not None else ACK_WAIT_SECONDS
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        session = session_factory()
        try:
            if _ack_covers_maintenance(session):
                return True
        finally:
            session.close()
        time.sleep(_ACK_POLL_SECONDS)
    return False


def _ack_covers_maintenance(db: Session) -> bool:
    if not worker_maintenance_active(db):
        return False
    at_row = db.get(Setting, MAINTENANCE_AT_KEY)
    ack_row = db.get(Setting, ACK_KEY)
    if at_row is None or not at_row.value or ack_row is None or not ack_row.value:
        return False
    try:
        started = _aware(datetime.fromisoformat(at_row.value))
        acked = _aware(datetime.fromisoformat(ack_row.value))
    except ValueError:
        return False
    return acked >= started


def _clear_ack(db: Session, _moment: datetime) -> None:
    row = db.get(Setting, ACK_KEY)
    if row is not None:
        db.delete(row)


def _put(db: Session, key: str, value: str, moment: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=moment))
    else:
        row.value = value
        row.updated_at = moment


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
