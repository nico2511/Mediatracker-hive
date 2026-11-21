"""Délais croissants entre deux tours complets des sources (option C)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.constants import BACKOFFS, REDISCOVER, REDISCOVER_MESSAGE
from mediatracker.core.pipeline.download_pass_errors import _PASS_ATTR, _PassVerdict
from mediatracker.core.pipeline.jobs import _aware, _fast_source_poll, _throttle_poll
from mediatracker.db.models import Download, Media

MAX_SOURCE_ROUND_TOURS = 4
DAILY_SOURCE_ROUND_GAP = timedelta(hours=24)
def source_round_delay(completed_rounds: int) -> timedelta:
    if completed_rounds <= 0:
        return timedelta(0)
    if completed_rounds <= len(BACKOFFS):
        return BACKOFFS[completed_rounds - 1]
    return DAILY_SOURCE_ROUND_GAP


def reset_source_rounds(row: Download) -> None:
    row.source_rounds = 0


def is_poll_style_empty_pass(row: Download, code: str) -> bool:
    from mediatracker.core.source_poll import _failed_is_poll_requeue_eligible

    if code in {"NO_LINK", "PROFILE", "POLL_PROFILE_MISSING"}:
        return True
    if _failed_is_poll_requeue_eligible(row):
        return True
    bag = getattr(row, _PASS_ATTR, None)
    if not isinstance(bag, dict) or not bag:
        return False
    return all(isinstance(v, _PassVerdict) and v.issue in {"empty", "refused"} for v in bag.values())


def requeue_allowed_after_failure(row: Download, now: datetime, *, fresh_links: bool) -> bool:
    if fresh_links:
        return True
    rounds = int(row.source_rounds or 0)
    if rounds <= 0:
        return True
    finished = _aware(row.finished_at)
    if finished is None:
        return True
    return now >= finished + source_round_delay(rounds)


def schedule_after_empty_source_round(
    db: Session,
    row: Download,
    media: Media,
    settings: Settings,
    now: datetime,
    *,
    fast_wawacity_poll: bool,
) -> None:
    row.source_rounds = int(row.source_rounds or 0) + 1
    row.state = "pending"
    row.error_code = REDISCOVER
    row.error = REDISCOVER_MESSAGE
    row.finished_at = None
    row.handle = None
    row.attempt_started_at = None
    row.next_retry_at = now + source_round_delay(row.source_rounds)
    if fast_wawacity_poll and row.source_rounds <= 1:
        _fast_source_poll(db, media, settings, now)
    elif row.source_rounds > 1:
        _throttle_poll(db, media, settings, now)


def wake_pending_after_fresh_links(
    db: Session,
    item_id: int,
    settings: Settings,
    now: datetime,
    *,
    fresh_links: bool,
) -> None:
    if not fresh_links:
        return
    from mediatracker.core.pipeline.download_guard import active_download_for_item

    row = active_download_for_item(db, item_id)
    if row is None or row.state != "pending":
        return
    if not row.source_rounds and (_aware(row.next_retry_at) is None or _aware(row.next_retry_at) <= now):
        return
    reset_source_rounds(row)
    row.next_retry_at = now
    row.error_code = None
    row.error = None
    from mediatracker.core.worker_wake import REASON_API_QUEUE, wake_worker

    wake_worker(db, settings, REASON_API_QUEUE, commit=False)


def force_source_search(
    db: Session,
    row: Download,
    settings: Settings,
    now: datetime,
) -> None:
    from mediatracker.core.pipeline.download_guard import DOWNLOAD_IN_PROGRESS_STATES

    if row.state not in DOWNLOAD_IN_PROGRESS_STATES:
        raise RuntimeError("NOT_FORCEABLE")
    reset_source_rounds(row)
    row.state = "pending"
    row.error_code = REDISCOVER
    row.error = REDISCOVER_MESSAGE
    row.next_retry_at = now
    row.finished_at = None
    row.handle = None
    row.attempt_started_at = None
    row.skipped_link_ids = []
    from mediatracker.core.worker_wake import REASON_API_QUEUE, wake_worker

    wake_worker(db, settings, REASON_API_QUEUE, commit=False)