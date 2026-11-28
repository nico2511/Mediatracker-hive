"""Bounded, non-blocking wait for AllDebrid magnet preparation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.jobs import _aware
from mediatracker.db.models import Download, MediaItem
from mediatracker.settings_int import int_setting

logger = logging.getLogger(__name__)

ALLDEBRID_WAIT_CODE = "ALLDEBRID_WAIT"
ALLDEBRID_WAIT_MESSAGE = "En attente AllDebrid."
MAGNET_TIMEOUT_CODE = "MAGNET_TIMEOUT"

SETTING_WAIT_MAX = "alldebrid_magnet_wait_max_seconds"
SETTING_POLL = "alldebrid_magnet_poll_seconds"


def magnet_wait_max_seconds(db: Session, settings: Settings) -> int:
    return int_setting(db, SETTING_WAIT_MAX, settings.alldebrid_magnet_wait_max_seconds)


def magnet_poll_interval(db: Session, settings: Settings) -> timedelta:
    seconds = int_setting(db, SETTING_POLL, settings.alldebrid_magnet_poll_seconds)
    return timedelta(seconds=max(5, seconds))


def is_magnet_wait(row: Download) -> bool:
    return row.error_code == ALLDEBRID_WAIT_CODE and bool((row.debrid_magnet_id or "").strip())


def schedule_magnet_poll_retry(
    row: Download,
    now: datetime,
    db: Session,
    settings: Settings,
) -> None:
    row.next_retry_at = now + magnet_poll_interval(db, settings)


def mark_magnet_waiting(
    row: Download,
    magnet_id: str,
    now: datetime,
    db: Session,
    settings: Settings,
    *,
    reset_budget: bool = False,
) -> None:
    previous = (row.debrid_magnet_id or "").strip()
    row.debrid_magnet_id = magnet_id
    row.error_code = ALLDEBRID_WAIT_CODE
    row.error = ALLDEBRID_WAIT_MESSAGE
    row.state = "pending"
    if reset_budget or not previous or previous != magnet_id:
        row.attempt_started_at = now
    schedule_magnet_poll_retry(row, now, db, settings)


def magnet_wait_expired(row: Download, now: datetime, db: Session, settings: Settings) -> bool:
    started = _aware(row.attempt_started_at)
    if started is None:
        return False
    return (now - started).total_seconds() >= magnet_wait_max_seconds(db, settings)


def clear_magnet_wait_flags(row: Download) -> None:
    row.error_code = None
    row.error = None


def episode_slot_label(item: MediaItem) -> str:
    if item.season is not None and item.episode is not None:
        return f"S{item.season:02d}E{item.episode:02d}"
    return "film"


def log_magnet_wait_begin(
    row: Download,
    item: MediaItem,
    db: Session,
    settings: Settings,
) -> None:
    max_s = magnet_wait_max_seconds(db, settings)
    logger.info(
        "AllDebrid — attente aimant démarrée. job=%s episode=%s délai_max=%ss",
        row.id,
        episode_slot_label(item),
        max_s,
    )


def log_magnet_wait_end(
    row: Download,
    item: MediaItem,
    now: datetime,
    *,
    ended: str,
    next_step: str,
) -> None:
    started = _aware(row.attempt_started_at)
    elapsed_s = int((now - started).total_seconds()) if started is not None else 0
    logger.info(
        "AllDebrid — attente aimant %s. job=%s episode=%s durée=%ss suite=%s",
        ended,
        row.id,
        episode_slot_label(item),
        elapsed_s,
        next_step,
    )


def abandon_magnet_wait(debrid, row: Download) -> None:
    magnet_id = (row.debrid_magnet_id or "").strip()
    if magnet_id and hasattr(debrid, "delete_magnet"):
        try:
            debrid.delete_magnet(magnet_id)
        except Exception:
            pass
    row.debrid_magnet_id = None
    clear_magnet_wait_flags(row)