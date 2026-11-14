"""Jellyfin webhook token: header-first auth and deprecated query tracking."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

WEBHOOK_TOKEN_HEADER = "X-MediaTracker-Token"
WEBHOOK_QUERY_TOKEN_LAST_AT = "jellyfin.webhook_query_token_last_at"
_QUERY_WARN_INTERVAL = timedelta(hours=1)
_QUERY_PERSIST_INTERVAL = timedelta(minutes=10)
_QUERY_WARNING_TTL = timedelta(days=7)

_LOG = logging.getLogger("mediatracker.integrations.jellyfin_webhook")
_last_query_warn_at: dict[str, datetime] = {}


def resolve_webhook_token(header: str | None, query: str | None) -> tuple[str, bool]:
    """Return (credential, used_query). Header wins when both are sent."""
    text_header = (header or "").strip()
    text_query = (query or "").strip()
    if text_header:
        return text_header, False
    if text_query:
        return text_query, True
    return "", False


def webhook_token_valid(expected: str, sent: str) -> bool:
    if not expected or not sent:
        return False
    if len(sent) != len(expected):
        return False
    return secrets.compare_digest(sent, expected)


def note_webhook_query_token_usage(db: Session, token: str, moment: datetime) -> None:
    """Persist last query usage and emit a rate-limited warning (token never logged)."""
    last_persisted = _parse_time(_text(db, WEBHOOK_QUERY_TOKEN_LAST_AT))
    if last_persisted is None or moment - last_persisted >= _QUERY_PERSIST_INTERVAL:
        _remember(db, WEBHOOK_QUERY_TOKEN_LAST_AT, moment.isoformat(), moment)
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    last = _last_query_warn_at.get(fingerprint)
    if last is not None and moment - last < _QUERY_WARN_INTERVAL:
        return
    _last_query_warn_at[fingerprint] = moment
    _LOG.warning(
        "Webhook Jellyfin : jeton transmis en paramètre d'URL (?token=). Préférez l'en-tête %s.",
        WEBHOOK_TOKEN_HEADER,
    )


def webhook_query_token_warning(db: Session, now: datetime | None = None) -> str | None:
    moment = _parse_time(_text(db, WEBHOOK_QUERY_TOKEN_LAST_AT))
    if moment is None:
        return None
    reference = now or datetime.now(timezone.utc)
    if reference - moment > _QUERY_WARNING_TTL:
        return None
    stamp = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Un client utilise encore le jeton dans l'URL (dernier usage : {stamp})."


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _remember(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def reset_query_warn_cache_for_tests() -> None:
    _last_query_warn_at.clear()
