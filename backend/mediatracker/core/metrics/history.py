"""Five-minute metrics history in SQLite (one write per bucket)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.metrics.collector import MetricsCollector
from mediatracker.core.metrics.worker_counters import public_snapshot
from mediatracker.core.worker_wake import wake_counts
from mediatracker.db.models import MetricsHistory, Setting
from mediatracker.retention import METRICS_HISTORY_DAYS

logger = logging.getLogger(__name__)

BUCKET_MINUTES = 5
RETENTION_DAYS = METRICS_HISTORY_DAYS
_LAST_AT_KEY = "metrics.history_last_at"
_PURGE_AT_KEY = "metrics.history_purge_at"

_sampler = MetricsCollector(interval_s=float(BUCKET_MINUTES * 60))


def metrics_history_due(db: Session, now: datetime) -> bool:
    moment = _aware(now)
    last = _parse_stamp(db, _LAST_AT_KEY)
    if last is None:
        return False
    return moment - last >= timedelta(minutes=BUCKET_MINUTES)


def next_metrics_history_deadline(db: Session, now: datetime) -> datetime | None:
    moment = _aware(now)
    last = _parse_stamp(db, _LAST_AT_KEY)
    if last is None:
        return moment + timedelta(minutes=BUCKET_MINUTES)
    target = last + timedelta(minutes=BUCKET_MINUTES)
    if target <= moment:
        return None
    return target


def maybe_record_metrics_history(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> bool:
    """Insert one history row when the five-minute bucket is due."""
    moment = _aware(now or datetime.now(timezone.utc))
    if not _should_record_bucket(db, moment):
        return False
    bucket = _floor_bucket(moment)
    existing = db.scalar(select(MetricsHistory.id).where(MetricsHistory.bucket_at == bucket).limit(1))
    if existing is not None:
        _touch_stamp(db, _LAST_AT_KEY, moment)
        db.flush()
        return False
    point = _sampler._build_point(db, settings)
    worker = public_snapshot(db)
    wakes = wake_counts(db)
    row = MetricsHistory(
        bucket_at=bucket,
        cpu_api=_cpu(point, "api"),
        cpu_worker=_cpu(point, "worker"),
        rss_api=_rss(point, "api"),
        rss_worker=_rss(point, "worker"),
        worker_wakes=dict(wakes),
        source_requests=dict(worker.get("source_requests") or {}),
        task_durations=dict(worker.get("task_durations") or {}),
    )
    db.add(row)
    _touch_stamp(db, _LAST_AT_KEY, moment)
    db.flush()
    maybe_purge_metrics_history(db, now=moment)
    return True


def maybe_purge_metrics_history(db: Session, *, now: datetime | None = None) -> int:
    moment = _aware(now or datetime.now(timezone.utc))
    last = _parse_stamp(db, _PURGE_AT_KEY)
    if last is not None and moment - last < timedelta(days=1):
        return 0
    cutoff = moment - timedelta(days=RETENTION_DAYS)
    result = db.execute(delete(MetricsHistory).where(MetricsHistory.bucket_at < cutoff))
    removed = int(result.rowcount or 0)
    _touch_stamp(db, _PURGE_AT_KEY, moment)
    if removed:
        logger.info("Historique métriques : %s ligne(s) de plus de %s jours supprimée(s).", removed, RETENTION_DAYS)
    return removed


def fetch_history(
    db: Session,
    *,
    since: datetime,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    start = _aware(since)
    end = _aware(until) if until is not None else datetime.now(timezone.utc)
    rows = db.scalars(
        select(MetricsHistory)
        .where(MetricsHistory.bucket_at >= start, MetricsHistory.bucket_at <= end)
        .order_by(MetricsHistory.bucket_at),
    ).all()
    return [_row_dict(row) for row in rows]


def history_export_payload(db: Session, *, since: datetime, until: datetime | None = None) -> dict[str, Any]:
    return {
        "since": _aware(since).isoformat(),
        "until": (_aware(until) if until is not None else datetime.now(timezone.utc)).isoformat(),
        "bucket_minutes": BUCKET_MINUTES,
        "points": fetch_history(db, since=since, until=until),
    }


def reset_metrics_history(db: Session) -> None:
    """Drop history rows and schedule stamps. Tests only."""
    db.execute(delete(MetricsHistory))
    for key in (_LAST_AT_KEY, _PURGE_AT_KEY):
        row = db.get(Setting, key)
        if row is not None:
            db.delete(row)
    db.flush()


def _row_dict(row: MetricsHistory) -> dict[str, Any]:
    return {
        "bucket_at": row.bucket_at.isoformat(),
        "cpu_api": row.cpu_api,
        "cpu_worker": row.cpu_worker,
        "rss_api": row.rss_api,
        "rss_worker": row.rss_worker,
        "worker_wakes": row.worker_wakes or {},
        "source_requests": row.source_requests or {},
        "task_durations": row.task_durations or {},
    }


def _cpu(point: dict[str, Any], role: str) -> float | None:
    processes = point.get("processes") or {}
    proc = processes.get(role) or {}
    value = proc.get("cpu_percent")
    return float(value) if value is not None else None


def _rss(point: dict[str, Any], role: str) -> int | None:
    processes = point.get("processes") or {}
    proc = processes.get(role) or {}
    value = proc.get("rss_bytes")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _should_record_bucket(db: Session, moment: datetime) -> bool:
    last = _parse_stamp(db, _LAST_AT_KEY)
    if last is None:
        return True
    return moment - last >= timedelta(minutes=BUCKET_MINUTES)


def _floor_bucket(moment: datetime) -> datetime:
    aware = _aware(moment)
    minute = (aware.minute // BUCKET_MINUTES) * BUCKET_MINUTES
    return aware.replace(minute=minute, second=0, microsecond=0)


def _parse_stamp(db: Session, key: str) -> datetime | None:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return None
    try:
        return _aware(datetime.fromisoformat(row.value))
    except ValueError:
        return None


def _touch_stamp(db: Session, key: str, moment: datetime) -> None:
    text = _aware(moment).isoformat()
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=text, is_secret=False, updated_at=_aware(moment)))
        return
    row.value = text
    row.updated_at = _aware(moment)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
