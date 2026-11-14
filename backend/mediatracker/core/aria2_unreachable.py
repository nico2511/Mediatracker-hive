"""Track prolonged aria2 RPC outages from existing worker polls (no extra calls)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting
from mediatracker.events import record_event

SINCE_KEY = "system.aria2_unreachable_since"
ALERTED_KEY = "system.aria2_unreachable_alerted"


def threshold_minutes(db: Session, settings: Settings) -> int:
    return _int_setting(db, "aria2_unreachable_alert_min", settings.aria2_unreachable_alert_min)


def unreachable_minutes(db: Session, now: datetime) -> int | None:
    """Whole minutes since the current outage began, or None when aria2 is considered up."""
    since = _read_since(db)
    if since is None:
        return None
    moment = _aware(now)
    elapsed = moment - since
    if elapsed.total_seconds() < 0:
        return 0
    return int(elapsed.total_seconds() // 60)


def note_unreachable(db: Session, settings: Settings, now: datetime) -> None:
    moment = _aware(now)
    since = _read_since(db)
    if since is None:
        _write_text(db, SINCE_KEY, moment.isoformat())
        since = moment
    minutes = unreachable_minutes(db, moment)
    if minutes is None:
        return
    limit = threshold_minutes(db, settings)
    if limit <= 0 or minutes < limit:
        return
    if _flag(db, ALERTED_KEY):
        return
    record_event(
        db,
        "aria2.unreachable",
        level="warning",
        dedupe_key=f"aria2.unreachable:{int(since.timestamp())}",
        when=moment,
        payload={
            "title": "aria2",
            "message": f"aria2 est injoignable depuis {minutes} min.",
            "minutes": minutes,
            "threshold_minutes": limit,
            "level": "warning",
        },
    )
    _write_text(db, ALERTED_KEY, "1")


def note_reachable(db: Session, settings: Settings, now: datetime) -> None:
    del settings
    moment = _aware(now)
    since = _read_since(db)
    if since is None:
        return
    if _flag(db, ALERTED_KEY):
        minutes = unreachable_minutes(db, moment)
        record_event(
            db,
            "aria2.reachable",
            level="info",
            dedupe_key=f"aria2.reachable:{int(since.timestamp())}",
            when=moment,
            payload={
                "title": "aria2",
                "message": "aria2 répond à nouveau.",
                "minutes": minutes,
                "level": "info",
            },
        )
    _write_text(db, SINCE_KEY, "")
    _write_text(db, ALERTED_KEY, "0")


def aria2_down_message(db: Session, settings: Settings, now: datetime) -> str:
    minutes = unreachable_minutes(db, now)
    if minutes is not None and minutes > 0:
        return f"aria2 injoignable depuis {minutes} min."
    return "aria2 est injoignable."


def _read_since(db: Session) -> datetime | None:
    raw = _text(db, SINCE_KEY)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return _aware(parsed)


def _flag(db: Session, key: str) -> bool:
    return _text(db, key) == "1"


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _write_text(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    moment = datetime.now(timezone.utc)
    if row is None:
        if not value:
            return
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=moment))
        return
    row.value = value
    row.updated_at = moment


def _int_setting(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or row.value in (None, ""):
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
