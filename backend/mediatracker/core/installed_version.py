"""Detect a new container image after Watchtower (or manual pull) and optionally notify."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.build_info import app_version, build_date, fingerprint_suffix
from mediatracker.db.models import Event, Setting

logger = logging.getLogger(__name__)

FINGERPRINT_KEY = "system.installed_fingerprint"
APP_VERSION_KEY = "system.app_version"
NOTIFY_SETTING_KEY = "notify_on_update"


def installed_fingerprint() -> str:
    version = app_version()
    return f"{version}@{fingerprint_suffix()}"


def _split_fingerprint(raw: str) -> tuple[str, str | None]:
    if "@" not in raw:
        return raw, None
    version, suffix = raw.split("@", 1)
    return version, suffix or None


_GIT_SUFFIX = re.compile(r"^[0-9a-f]{7}$")


def _sha_from_suffix(suffix: str | None) -> str | None:
    if suffix and _GIT_SUFFIX.fullmatch(suffix):
        return suffix
    return None


def _build_id_from_suffix(suffix: str | None) -> str | None:
    if not suffix or _sha_from_suffix(suffix):
        return None
    return suffix


def _notify_on_update_enabled(db: Session) -> bool:
    row = db.get(Setting, NOTIFY_SETTING_KEY)
    return row is not None and row.value == "true"


def _store_fingerprint(db: Session, value: str, moment: datetime) -> None:
    row = db.get(Setting, FINGERPRINT_KEY)
    if row is None:
        db.add(Setting(key=FINGERPRINT_KEY, value=value, is_secret=False, updated_at=moment))
        return
    row.value = value
    row.updated_at = moment


def _touch_app_version(db: Session, moment: datetime) -> None:
    row = db.get(Setting, APP_VERSION_KEY)
    value = app_version()
    if row is None:
        db.add(Setting(key=APP_VERSION_KEY, value=value, is_secret=False, updated_at=moment))
    else:
        row.value = value
        row.updated_at = moment


def _record_updated_event(db: Session, *, payload: dict, dedupe_key: str, moment: datetime) -> Event:
    existing = db.scalar(select(Event).where(Event.type == "system.updated", Event.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    event = Event(
        ts=moment,
        type="system.updated",
        level="info",
        media_id=None,
        item_id=None,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    try:
        with db.begin_nested():
            db.add(event)
            db.flush()
    except IntegrityError:
        found = db.scalar(select(Event).where(Event.type == "system.updated", Event.dedupe_key == dedupe_key))
        if found is not None:
            return found
        raise
    if _notify_on_update_enabled(db):
        try:
            from mediatracker.events.deliver import schedule_deliveries

            schedule_deliveries(db, event, moment)
        except Exception:
            logger.exception("Planification de la notification de mise à jour installée ignorée.")
    return event


def observe_installed_version(db: Session, _settings: Settings) -> None:
    """Compare the running image to the last stored fingerprint. Never raises."""
    moment = datetime.now(timezone.utc)
    try:
        current = installed_fingerprint()
        _touch_app_version(db, moment)
        row = db.get(Setting, FINGERPRINT_KEY)
        previous = (row.value or "").strip() if row is not None else ""
        if not previous:
            _store_fingerprint(db, current, moment)
            db.commit()
            return
        if previous == current:
            db.commit()
            return
        prev_version, prev_suffix = _split_fingerprint(previous)
        new_version, new_suffix = _split_fingerprint(current)
        _store_fingerprint(db, current, moment)
        payload = {
            "title": "MediaTracker",
            "previous_version": prev_version,
            "previous_sha": _sha_from_suffix(prev_suffix),
            "previous_build_id": _build_id_from_suffix(prev_suffix),
            "new_version": new_version,
            "new_sha": _sha_from_suffix(new_suffix),
            "new_build_id": _build_id_from_suffix(new_suffix),
            "new_build_date": build_date(),
            "level": "info",
        }
        _record_updated_event(db, payload=payload, dedupe_key=f"system.updated:{current}", moment=moment)
        db.commit()
    except Exception:
        logger.exception("Observation de la version installée ignorée.")
        db.rollback()
