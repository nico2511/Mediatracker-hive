"""Interrupteur de collecte task_run (durée limitée ou sans limite)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

COLLECTION_UNTIL_KEY = "task_run.collection_until"
COLLECTION_CHOICE_KEY = "task_run.collection_choice"

DurationChoice = Literal["off", "1h", "6h", "24h", "unlimited"]

_UNLIMITED = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def _parse_until(raw: str | None) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if text == "unlimited":
        return _UNLIMITED
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_collection_active(db: Session, *, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    row = db.get(Setting, COLLECTION_UNTIL_KEY)
    if row is None or not (row.value or "").strip():
        return False
    until = _parse_until(row.value)
    if until is None:
        return False
    return moment < until


def collection_status(db: Session, *, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    row = db.get(Setting, COLLECTION_UNTIL_KEY)
    raw = (row.value or "").strip() if row is not None else ""
    until = _parse_until(raw) if raw else None
    active = until is not None and moment < until
    mode = "off"
    if raw == "unlimited" or (until is not None and until >= _UNLIMITED - timedelta(days=1)):
        mode = "unlimited"
    elif active and until is not None:
        mode = "timed"
    choice_raw = db.get(Setting, COLLECTION_CHOICE_KEY)
    choice = (choice_raw.value or "").strip() if choice_raw is not None else ""
    if not active:
        choice = "off"
    elif not choice:
        choice = "unlimited" if mode == "unlimited" else "timed"
    return {
        "active": active,
        "until": None if until is None or mode == "unlimited" else until.isoformat(),
        "mode": mode,
        "duration": choice if choice in {"off", "1h", "6h", "24h", "unlimited"} else None,
    }


def set_collection_duration(db: Session, choice: DurationChoice, *, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    row = db.get(Setting, COLLECTION_UNTIL_KEY)
    if row is None:
        row = Setting(key=COLLECTION_UNTIL_KEY, value="", is_secret=False)
        db.add(row)
    choice_row = db.get(Setting, COLLECTION_CHOICE_KEY)
    if choice_row is None:
        choice_row = Setting(key=COLLECTION_CHOICE_KEY, value="", is_secret=False)
        db.add(choice_row)
    if choice == "off":
        row.value = ""
        choice_row.value = ""
        row.updated_at = moment
        choice_row.updated_at = moment
        db.flush()
        return collection_status(db, now=moment)
    choice_row.value = choice
    choice_row.updated_at = moment
    if choice == "unlimited":
        row.value = "unlimited"
    elif choice == "1h":
        row.value = (moment + timedelta(hours=1)).isoformat()
    elif choice == "6h":
        row.value = (moment + timedelta(hours=6)).isoformat()
    elif choice == "24h":
        row.value = (moment + timedelta(hours=24)).isoformat()
    else:
        raise ValueError(f"Durée inconnue : {choice}")
    row.updated_at = moment
    db.flush()
    return collection_status(db, now=moment)