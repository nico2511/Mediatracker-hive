"""Drop journal rows older than the retention window (batched, once per day)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.db.models import Event, Setting
from mediatracker.retention import EVENT_JOURNAL_DAYS

logger = logging.getLogger(__name__)

RETENTION_DAYS = EVENT_JOURNAL_DAYS
_BATCH_SIZE = 500
_STAMP = "event_retention_at"


def maybe_purge_old_events(db: Session, *, now: datetime | None = None, batch_size: int = _BATCH_SIZE) -> int:
    """Remove events older than ``RETENTION_DAYS``. Runs at most once per UTC day."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if _too_soon(db, moment):
        return 0
    cutoff = moment - timedelta(days=RETENTION_DAYS)
    removed = 0
    while True:
        ids = list(
            db.scalars(
                select(Event.id).where(Event.ts < cutoff).order_by(Event.id).limit(batch_size),
            ).all(),
        )
        if not ids:
            break
        db.execute(delete(Event).where(Event.id.in_(ids)))
        removed += len(ids)
        if len(ids) < batch_size:
            break
    _remember(db, moment)
    if removed:
        logger.info(
            "Journal : %s événement(s) de plus de %s jours supprimé(s).",
            removed,
            RETENTION_DAYS,
        )
    return removed


def _too_soon(db: Session, moment: datetime) -> bool:
    last = _read_stamp(db)
    return last is not None and moment - last < timedelta(days=1)


def _read_stamp(db: Session) -> datetime | None:
    row = db.get(Setting, _STAMP)
    if row is None or not row.value:
        return None
    try:
        last = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last


def _remember(db: Session, moment: datetime) -> None:
    row = db.get(Setting, _STAMP)
    if row is None:
        db.add(Setting(key=_STAMP, value=moment.isoformat(), is_secret=False, updated_at=moment))
    else:
        row.value = moment.isoformat()
        row.updated_at = moment
