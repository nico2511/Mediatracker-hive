"""Internal event outbox. Delivery fans out to webhook subscriptions."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.db.models import Event, Media, MediaItem
from mediatracker.events.catalog import LIBRARY_IMPORTED

JOURNAL_FOLD_TYPES = frozenset({"source.checked", "catalog.failed"})


def _journal_fingerprint(type_: str, payload: dict[str, Any]) -> str:
    if type_ == "source.checked":
        return f"{payload.get('result')}|{payload.get('message')}"
    if type_ == "catalog.failed":
        return str(payload.get("message") or "")
    return ""


def _target_stmt(
    media_id: int | None,
    item_id: int | None,
):
    stmt = select(Event)
    if media_id is None:
        stmt = stmt.where(Event.media_id.is_(None))
    else:
        stmt = stmt.where(Event.media_id == media_id)
    if item_id is None:
        stmt = stmt.where(Event.item_id.is_(None))
    else:
        stmt = stmt.where(Event.item_id == item_id)
    return stmt


def _try_fold_journal(
    db: Session,
    type_: str,
    *,
    media_id: int | None,
    item_id: int | None,
    payload: dict[str, Any],
    moment: datetime,
) -> Event | None:
    last = db.scalar(_target_stmt(media_id, item_id).order_by(Event.id.desc()).limit(1))
    if last is None or last.type != type_:
        return None
    needle = _journal_fingerprint(type_, payload)
    if not needle or needle != _journal_fingerprint(type_, last.payload or {}):
        return None
    last.repeat_count = max(last.repeat_count, 1) + 1
    last.last_seen_at = moment
    last.ts = moment
    db.flush()
    return last


def record_event(
    db: Session,
    type_: str,
    *,
    level: str = "info",
    media_id: int | None = None,
    item_id: int | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    when: datetime | None = None,
) -> Event:
    moment = when or datetime.now(timezone.utc)
    body = payload or {}
    if dedupe_key is None and type_ in JOURNAL_FOLD_TYPES:
        folded = _try_fold_journal(
            db,
            type_,
            media_id=media_id,
            item_id=item_id,
            payload=body,
            moment=moment,
        )
        if folded is not None:
            return folded
    if dedupe_key:
        existing = db.scalar(select(Event).where(Event.type == type_, Event.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
    event = Event(
        ts=moment,
        type=type_,
        level=level,
        media_id=media_id,
        item_id=item_id,
        payload=body,
        dedupe_key=dedupe_key,
        repeat_count=1,
        last_seen_at=moment,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        found = db.scalar(select(Event).where(Event.type == type_, Event.dedupe_key == dedupe_key))
        if found is not None:
            return found
        raise
    from mediatracker.events.deliver import schedule_deliveries

    schedule_deliveries(db, event, moment)
    return event


def record_library_imported(db: Session, media: Media, item: MediaItem, path: str) -> Event:
    """Outbox row for a file that just landed on the NAS.

    ntfy and Home Assistant subscriptions use this event. ``poster_url`` is
    included so a notification can show the TMDB poster without another lookup.
    """
    return record_event(
        db,
        LIBRARY_IMPORTED,
        level="info",
        media_id=media.id,
        item_id=item.id,
        payload={
            "media_id": media.id,
            "item_id": item.id,
            "title": media.title,
            "media_type": media.type,
            "season": item.season,
            "episode": item.episode,
            "episode_title": item.title,
            "path": path,
            "tmdb_id": media.tmdb_id,
            "poster_url": media.poster_url,
            "level": "info",
        },
    )
