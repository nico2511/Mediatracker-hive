"""Journal listing: filters, cursor pagination, and safe payloads."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String, cast, func, or_, select, tuple_
from sqlalchemy.orm import Session

from mediatracker.api.errors import Problem
from mediatracker.db.models import Event
from mediatracker.events.catalog import EVENT_TYPES
from mediatracker.events.scrub import scrub_payload
from mediatracker.events.text import event_label

SEARCH_MAX_LEN = 80
_LEGACY_CURSOR = re.compile(r"^\d+$")


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def encode_cursor(ts: datetime, event_id: int) -> str:
    epoch_ms = int(as_utc(ts).timestamp() * 1000)
    raw = f"{epoch_ms}:{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> tuple[datetime | None, int]:
    if _LEGACY_CURSOR.fullmatch(value):
        parsed = int(value)
        if parsed < 0:
            raise Problem(422, "Requête invalide", "Curseur invalide.")
        return None, parsed
    pad = "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(value + pad).decode()
        epoch_ms_str, id_str = raw.split(":", 1)
        epoch_ms = int(epoch_ms_str)
        event_id = int(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Problem(422, "Requête invalide", "Curseur invalide.") from exc
    if event_id < 0:
        raise Problem(422, "Requête invalide", "Curseur invalide.")
    ts = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return ts, event_id


def validate_search(query: str | None) -> str | None:
    if query is None:
        return None
    trimmed = query.strip()
    if not trimmed:
        return None
    if len(trimmed) > SEARCH_MAX_LEN:
        raise Problem(
            422,
            "Requête invalide",
            f"La recherche est limitée à {SEARCH_MAX_LEN} caractères.",
        )
    return trimmed


def types_for_label_search(query: str) -> list[str]:
    needle = query.casefold()
    hits: list[str] = []
    for event_type in EVENT_TYPES:
        label = event_label(event_type, {})
        if needle in event_type.casefold() or needle in label.casefold():
            hits.append(event_type)
    return hits


def list_journal_events(
    db: Session,
    *,
    limit: int,
    cursor: str | None,
    event_type: str | None,
    category: str | None,
    media_id: int | None,
    download_id: int | None,
    level: str | None,
    since: datetime | None,
    until: datetime | None,
    search: str | None,
) -> tuple[list[Event], str | None]:
    if event_type is not None and event_type not in EVENT_TYPES:
        raise Problem(422, "Requête invalide", "Événement inconnu.")
    if category is not None and not category.strip():
        raise Problem(422, "Requête invalide", "Catégorie invalide.")
    stmt = select(Event)
    if event_type is not None:
        stmt = stmt.where(Event.type == event_type)
    if category is not None:
        stmt = stmt.where(Event.type.startswith(category))
    if media_id is not None:
        stmt = stmt.where(Event.media_id == media_id)
    if download_id is not None:
        if download_id < 0:
            raise Problem(422, "Requête invalide", "Téléchargement invalide.")
        stmt = stmt.where(func.json_extract(Event.payload, "$.download_id") == download_id)
    if level is not None:
        stmt = stmt.where(Event.level == level)
    if since is not None:
        stmt = stmt.where(Event.ts >= as_utc(since))
    if until is not None:
        stmt = stmt.where(Event.ts <= as_utc(until))
    if search is not None:
        stmt = _apply_search(stmt, search)
    if cursor is not None:
        cursor_ts, cursor_id = decode_cursor(cursor)
        if cursor_ts is None:
            stmt = stmt.where(Event.id < cursor_id)
        else:
            cursor_ts = as_utc(cursor_ts)
            stmt = stmt.where(
                tuple_(Event.ts, Event.id) < tuple_(cursor_ts, cursor_id),
            )
    rows = list(
        db.scalars(
            stmt.order_by(Event.ts.desc(), Event.id.desc()).limit(limit + 1),
        ).all(),
    )
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.ts, last.id)
    return rows, next_cursor


def event_out(row: Event) -> dict[str, Any]:
    safe = scrub_payload(row.payload or {})
    last_seen = row.last_seen_at or row.ts
    return {
        "id": row.id,
        "ts": as_utc(row.ts),
        "last_seen_at": as_utc(last_seen),
        "repeat_count": max(row.repeat_count or 1, 1),
        "type": row.type,
        "level": row.level,
        "media_id": row.media_id,
        "item_id": row.item_id,
        "label": event_label(row.type, safe),
        "payload": safe,
    }


def _apply_search(stmt, query: str):
    pattern = f"%{query.casefold()}%"
    type_hits = types_for_label_search(query)
    clauses = [
        func.lower(Event.type).like(pattern),
        func.lower(cast(Event.payload, String)).like(pattern),
    ]
    if type_hits:
        clauses.append(Event.type.in_(type_hits))
    return stmt.where(or_(*clauses))
