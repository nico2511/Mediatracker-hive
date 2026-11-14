"""Throttle Wawacity searches triggered by rediscover and version fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, Setting

REMOTE_SEARCH_COOLDOWN_HOURS_KEY = "remote_search_cooldown_hours"
REMOTE_SEARCH_COOLDOWN = timedelta(hours=6)
_REMOTE_SEARCH_COOLDOWN_HOURS_DEFAULT = int(REMOTE_SEARCH_COOLDOWN.total_seconds() // 3600)


def _int_setting(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def remote_search_cooldown_hours(db: Session, settings: Settings) -> int:
    return _int_setting(
        db,
        REMOTE_SEARCH_COOLDOWN_HOURS_KEY,
        settings.remote_search_cooldown_hours,
    )


def remote_search_cooldown(db: Session, settings: Settings) -> timedelta:
    return timedelta(hours=remote_search_cooldown_hours(db, settings))


def remote_search_limit_label(hours: int | None = None) -> str:
    """Human-readable French duration for logs and API messages."""
    value = _REMOTE_SEARCH_COOLDOWN_HOURS_DEFAULT if hours is None else hours
    return f"{value} h"


def remote_search_limit_phrase(db: Session, settings: Settings) -> str:
    return f"limite {remote_search_limit_label(remote_search_cooldown_hours(db, settings))}"


def remote_search_allowed(media: Media, now: datetime, *, cooldown: timedelta) -> bool:
    last = media.last_remote_search_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= cooldown


def begin_remote_search(media: Media, now: datetime, db: Session, settings: Settings) -> bool:
    """Reserve a search slot. Returns False when the per-fiche cooldown is active."""
    cooldown = remote_search_cooldown(db, settings)
    if not remote_search_allowed(media, now, cooldown=cooldown):
        return False
    media.last_remote_search_at = now
    return True
