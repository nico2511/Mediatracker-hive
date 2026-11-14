"""Series status from TMDB and stop following ended series that are complete."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.follow import unfollow_complete_series as _unfollow_complete_series
from mediatracker.core.polling import TERMINAL_STATUSES
from mediatracker.db.models import Media, MediaItem
from mediatracker.events import record_event
from mediatracker.integrations.tmdb import TmdbTitle

CompleteVia = str  # "watched" | "downloaded"


def apply_series_status_from_tmdb(db: Session, media: Media, fetched: TmdbTitle, now: datetime) -> bool:
    """Refresh ``series_status`` when TMDB sends a known value. Returns True if it changed."""
    if media.type != "series":
        return False
    incoming = fetched.series_status
    if not incoming or incoming == "unknown":
        return False
    previous = media.series_status
    if previous == incoming:
        return False
    media.series_status = incoming
    if previous in TERMINAL_STATUSES and incoming not in TERMINAL_STATUSES:
        media.series_auto_unfollow_key = None
    record_event(
        db,
        "media.series_status_changed",
        media_id=media.id,
        when=now,
        payload={"title": media.title, "previous": previous, "status": incoming},
    )
    return True


def stop_following_complete_series(
    db: Session,
    media: Media,
    *,
    today: date | None = None,
    emit_event: bool = True,
) -> bool:
    """Unfollow a terminal series once every aired episode (except season 0) is done."""
    if media.type != "series" or not media.followed:
        return False
    if (media.series_status or "") not in TERMINAL_STATUSES:
        return False
    moment = today or datetime.now(timezone.utc).date()
    items = list(
        db.scalars(
            select(MediaItem).where(
                MediaItem.media_id == media.id,
                MediaItem.season.is_not(None),
                MediaItem.episode.is_not(None),
                MediaItem.season != 0,
            )
        ).all()
    )
    aired = [item for item in items if _episode_aired(item, moment)]
    if not aired:
        return False
    if not all(_episode_done(db, item) for item in aired):
        return False
    fingerprint = _completion_fingerprint(media, aired, db)
    if media.series_auto_unfollow_key == fingerprint:
        return False
    via = _completion_via(aired, db)
    message = _auto_unfollow_message(via)
    if not _unfollow_complete_series(
        db,
        media,
        emit_event=emit_event,
        via=via,
        message=message,
    ):
        return False
    media.series_auto_unfollow_key = fingerprint
    return True


def _episode_aired(item: MediaItem, today: date) -> bool:
    if item.air_date is not None and item.air_date > today:
        return False
    return True


def series_all_aired_episodes_watched(
    episodes: list[MediaItem],
    *,
    today: date | None = None,
) -> bool:
    """True when every aired episode (season != 0) is marked watched."""
    moment = today or datetime.now(timezone.utc).date()
    candidates = [
        item
        for item in episodes
        if item.season is not None and item.episode is not None and item.season != 0
    ]
    aired = [item for item in candidates if _episode_aired(item, moment)]
    if not aired:
        return False
    return all(item.watched for item in aired)


def _episode_done(db: Session, item: MediaItem) -> bool:
    if item.watched:
        return True
    if item.downloaded or item.file_path:
        return True
    from mediatracker.core.source_poll import _library_has

    return _library_has(db, item)


def _episode_obtained(db: Session, item: MediaItem) -> bool:
    if item.downloaded or item.file_path:
        return True
    from mediatracker.core.source_poll import _library_has

    return _library_has(db, item)


def _completion_via(aired: list[MediaItem], db: Session) -> CompleteVia:
    if all(item.watched for item in aired):
        return "watched"
    return "downloaded"


def _completion_fingerprint(media: Media, aired: list[MediaItem], _db: Session) -> str:
    rows = [
        {
            "s": item.season,
            "e": item.episode,
            "a": item.air_date.isoformat() if item.air_date else None,
        }
        for item in sorted(aired, key=lambda row: (row.season or 0, row.episode or 0))
    ]
    payload = {"status": media.series_status or "", "episodes": rows}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _auto_unfollow_message(via: CompleteVia) -> str:
    if via == "watched":
        return "Suivi arrêté automatiquement : tous les épisodes diffusés ont été vus."
    return "Suivi arrêté automatiquement : tous les épisodes diffusés sont téléchargés."
