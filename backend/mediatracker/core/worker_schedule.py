"""When the worker may sleep: active aria2 cadence vs the next scheduled deadline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.aria2_events import active_download_tick_seconds
from mediatracker.core.catalog import catalog_clock
from mediatracker.core.cleanup_classify import should_run_classify
from mediatracker.core.dead_links import RECHECK_INTERVAL, RECHECK_KEY
from mediatracker.core.metrics.history import metrics_history_due, next_metrics_history_deadline
from mediatracker.core.pipeline.constants import ACTIVE
from mediatracker.core.source_force import FORCE_KEY
from mediatracker.core.source_poll import next_due
from mediatracker.db.models import Download, JellyfinPushJob, Media, Setting, WebhookDelivery
from mediatracker.integrations.jellyfin_sync import LAST_POLL_AT, REFRESH_AT
from mediatracker.library.scanner import LAST_SCAN_KEY

ACTIVE_TICK_SECONDS = 2.0
MAX_IDLE_SLEEP_SECONDS = 60.0


def has_active_downloads(db: Session) -> bool:
    row = db.scalar(select(Download.id).where(Download.state.in_(ACTIVE)).limit(1))
    return row is not None


def compute_sleep_seconds(db: Session, settings: Settings, now: datetime | None = None) -> float:
    """Seconds until the next worker loop. Active jobs use 2 s or 10 s aria2 polling."""
    moment = _aware(now or datetime.now(timezone.utc))
    if has_active_downloads(db):
        return active_download_tick_seconds(db)
    target = next_deadline(db, settings, moment)
    if target is None:
        return MAX_IDLE_SLEEP_SECONDS
    delta = (target - moment).total_seconds()
    if delta <= 0:
        return 0.0
    return min(delta, MAX_IDLE_SLEEP_SECONDS)


def next_deadline(db: Session, settings: Settings, now: datetime) -> datetime | None:
    """Earliest UTC instant that should end an idle sleep, or ``now`` if work is already due."""
    moment = _aware(now)
    candidates: list[datetime] = []

    if _work_due_now(db, settings, moment):
        return moment

    candidates.extend(_future_deadlines(db, settings, moment))
    future = [item for item in candidates if item > moment]
    if not future:
        return None
    return min(future)


def _work_due_now(db: Session, settings: Settings, now: datetime) -> bool:
    from mediatracker.hive.fingerprint import has_pending_hive_fingerprint
    from mediatracker.hive.identity import hive_enabled_in_db

    if hive_enabled_in_db(db) and has_pending_hive_fingerprint(db):
        return True
    if next_due(db, settings, now, commit=False) is not None:
        return True
    if _setting_nonempty(db, FORCE_KEY):
        return True
    ready = db.scalar(
        select(Download.id)
        .where(
            Download.state == "pending",
            or_(Download.next_retry_at.is_(None), Download.next_retry_at <= now),
        )
        .limit(1)
    )
    if ready is not None:
        return True
    if should_run_classify(now):
        return True
    refresh_at = _parse_setting_time(db, REFRESH_AT)
    if refresh_at is not None and refresh_at <= now:
        return True
    if metrics_history_due(db, now):
        return True
    return False


def _future_deadlines(db: Session, settings: Settings, now: datetime) -> list[datetime]:
    out: list[datetime] = []

    retry = db.scalar(
        select(func.min(Download.next_retry_at)).where(
            Download.state == "pending",
            Download.next_retry_at.is_not(None),
            Download.next_retry_at > now,
        )
    )
    if retry is not None:
        out.append(_aware(retry))

    poll = db.scalar(
        select(func.min(Media.next_source_poll_at)).where(
            Media.followed.is_(True),
            Media.on_hold.is_(False),
            Media.next_source_poll_at.is_not(None),
            Media.next_source_poll_at > now,
        )
    )
    if poll is not None:
        out.append(_aware(poll))

    _last, nxt, _err = catalog_clock(db)
    if nxt is not None and _aware(nxt) > now:
        out.append(_aware(nxt))

    digital = _parse_setting_time(db, "digital_releases.next_refresh_at")
    if digital is not None and digital > now:
        out.append(digital)

    refresh = _parse_setting_time(db, REFRESH_AT)
    if refresh is not None and refresh > now:
        out.append(refresh)

    poll_minutes = _int_setting(db, "jellyfin_poll_interval_min", settings.jellyfin_poll_interval_min)
    last_poll = _parse_setting_time(db, LAST_POLL_AT)
    if last_poll is not None:
        out.append(last_poll + timedelta(minutes=max(poll_minutes, 1)))

    scan_hours = _int_setting(db, "library_scan_interval_hours", settings.library_scan_interval_hours)
    last_scan = _parse_setting_time(db, LAST_SCAN_KEY)
    if last_scan is not None:
        out.append(last_scan + timedelta(hours=max(scan_hours, 1)))

    webhook = db.scalar(
        select(func.min(WebhookDelivery.next_attempt_at)).where(
            WebhookDelivery.status == "pending",
            WebhookDelivery.next_attempt_at.is_not(None),
            WebhookDelivery.next_attempt_at > now,
        )
    )
    if webhook is not None:
        out.append(_aware(webhook))

    push = db.scalar(
        select(func.min(JellyfinPushJob.next_retry_at)).where(
            JellyfinPushJob.next_retry_at.is_not(None),
            JellyfinPushJob.next_retry_at > now,
        )
    )
    if push is not None:
        out.append(_aware(push))

    incomplete_last = _parse_setting_time(db, "incomplete_cleanup_at")
    if incomplete_last is not None:
        out.append(incomplete_last + timedelta(hours=1))

    retention_last = _parse_setting_time(db, "event_retention_at")
    if retention_last is not None:
        out.append(retention_last + timedelta(days=1))

    dead_recheck = _parse_setting_time(db, RECHECK_KEY)
    if dead_recheck is not None:
        out.append(dead_recheck + RECHECK_INTERVAL)

    history_next = next_metrics_history_deadline(db, now)
    if history_next is not None:
        out.append(history_next)

    return out


def _setting_nonempty(db: Session, key: str) -> bool:
    row = db.get(Setting, key)
    return row is not None and bool(row.value)


def _parse_setting_time(db: Session, key: str) -> datetime | None:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return _aware(parsed)


def _int_setting(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
