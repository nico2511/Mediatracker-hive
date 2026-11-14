"""Throttle worker classify passes; wake immediately on library-changing events."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from mediatracker.config import Settings

_INTERVAL = timedelta(seconds=60)

_last_pass_at: datetime | None = None
_wake = False


def reset_classify_schedule() -> None:
    """Clear throttle state. Tests call this between cases."""
    global _last_pass_at, _wake
    _last_pass_at = None
    _wake = False


def request_classify(db: Session | None = None, settings: Settings | None = None) -> None:
    """Ask the worker to run classify on the next maintenance tick."""
    global _wake
    _wake = True
    if db is None:
        return
    from mediatracker.config import get_settings
    from mediatracker.core.worker_wake import REASON_CLASSIFY, wake_worker

    wake_worker(db, settings or get_settings(), REASON_CLASSIFY, commit=False)


def should_run_classify(now: datetime) -> bool:
    if _wake:
        return True
    if _last_pass_at is None:
        return True
    return now - _last_pass_at >= _INTERVAL


def mark_classify_pass(now: datetime) -> None:
    global _last_pass_at, _wake
    _last_pass_at = now
    _wake = False
