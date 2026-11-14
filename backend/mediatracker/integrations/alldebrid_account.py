"""AllDebrid account linking via PIN or manual key (stored encrypted, never exposed)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.clients import close_cached_clients
from mediatracker.db.models import Setting
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.integrations.alldebrid_key import (
    NEUTRAL_UNREADABLE_MESSAGE,
    db_key_unreadable,
    effective_alldebrid_key,
    env_api_key,
    provider_row,
)
from mediatracker.providers.debrid.alldebrid import AGENT, AllDebridClient, AllDebridError
from mediatracker.security.crypto import decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger(__name__)

PIN_SETTING_KEY = "alldebrid_pin_session"
PIN_EXPIRED_MESSAGE = "Le code a expiré. Aucune clé n'a été enregistrée."
_PIN_TERMINAL_ERRORS = frozenset({"PIN_EXPIRED", "PIN_INVALID", "PIN_ALREADY_AUTHED"})

AccountSource = Literal["env", "db", "none"]
PinPhase = Literal["idle", "pending", "expired"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def effective_source(settings: Settings, db: Session) -> AccountSource:
    if env_api_key(settings):
        return "env"
    if db_key_unreadable(settings, db):
        return "none"
    row = provider_row(db)
    if row is None or not row.enabled:
        return "none"
    if effective_alldebrid_key(settings, db):
        return "db"
    return "none"


def is_connected(settings: Settings, db: Session) -> bool:
    return effective_source(settings, db) != "none"


def _pin_client() -> AllDebridClient:
    return AllDebridClient("", agent=AGENT)


def _parse_expires_in(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 600
    return parsed if parsed > 0 else 600


def _parse_started_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _legacy_deadline(session: dict[str, Any]) -> datetime | None:
    started_at = _parse_started_at(session.get("started_at"))
    if started_at is None:
        return None
    return started_at + timedelta(seconds=_parse_expires_in(session.get("expires_in")))


def _ensure_expires_at(session: dict[str, Any]) -> None:
    raw = session.get("expires_at")
    if isinstance(raw, str) and raw:
        return
    legacy = _legacy_deadline(session)
    if legacy is not None:
        session["expires_at"] = legacy.isoformat()


def _parse_expires_at(session: dict[str, Any]) -> datetime | None:
    _ensure_expires_at(session)
    raw = session.get("expires_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _set_expires_at_from_remaining(session: dict[str, Any], remaining: int, *, now: datetime | None = None) -> None:
    moment = now or _utcnow()
    session["expires_at"] = (moment + timedelta(seconds=_parse_expires_in(remaining))).isoformat()


def session_expired_locally(session: dict[str, Any], *, now: datetime | None = None) -> bool:
    deadline = _parse_expires_at(session)
    if deadline is None:
        return False
    moment = now or _utcnow()
    return moment >= deadline


def _remaining_seconds(session: dict[str, Any], *, now: datetime | None = None) -> int:
    deadline = _parse_expires_at(session)
    if deadline is None:
        return _parse_expires_in(session.get("expires_in"))
    moment = now or _utcnow()
    return max(0, int((deadline - moment).total_seconds()))


def _load_pin_session(db: Session, settings: Settings) -> dict[str, Any] | None:
    row = db.get(Setting, PIN_SETTING_KEY)
    if row is None or not row.value:
        return None
    raw = row.value
    if is_encrypted(raw):
        try:
            raw = decrypt_secret(raw, settings.secret_key)
        except ValueError:
            logger.info("Session PIN AllDebrid illisible : suppression.")
            _clear_pin_session(db)
            db.commit()
            return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _clear_pin_session(db)
        db.commit()
        return None
    if not isinstance(parsed, dict):
        return None
    _ensure_expires_at(parsed)
    return parsed


def _save_pin_session(db: Session, settings: Settings, payload: dict[str, Any]) -> None:
    _ensure_expires_at(payload)
    now = _utcnow()
    encoded = json.dumps(payload, separators=(",", ":"))
    cipher = encrypt_secret(encoded, settings.secret_key)
    row = db.get(Setting, PIN_SETTING_KEY)
    if row is None:
        db.add(
            Setting(
                key=PIN_SETTING_KEY,
                value=cipher,
                is_secret=True,
                updated_at=now,
            )
        )
    else:
        row.value = cipher
        row.is_secret = True
        row.updated_at = now


def _clear_pin_session(db: Session) -> None:
    row = db.get(Setting, PIN_SETTING_KEY)
    if row is not None:
        db.delete(row)


def _apply_db_api_key(db: Session, settings: Settings, plain_key: str) -> None:
    row = provider_row(db)
    if row is None:
        raise ValueError("Fournisseur AllDebrid introuvable.")
    stored = encrypt_secret(plain_key.strip(), settings.secret_key)
    config = dict(row.config or {})
    config["api_key"] = stored
    row.config = config
    row.enabled = True
    db.commit()
    close_cached_clients()


def clear_db_api_key(db: Session, settings: Settings) -> None:
    row = provider_row(db)
    if row is None:
        return
    config = dict(row.config or {})
    config["api_key"] = ""
    row.config = config
    row.enabled = False
    db.commit()
    close_cached_clients()
    _clear_pin_session(db)
    db.commit()


def _pin_expired_snapshot(db: Session, settings: Settings) -> dict[str, Any]:
    _clear_pin_session(db)
    db.commit()
    return account_snapshot(
        settings,
        db,
        pin_phase="expired",
        pin_message=PIN_EXPIRED_MESSAGE,
    )


def start_pin(db: Session, settings: Settings) -> dict[str, Any]:
    if env_api_key(settings):
        raise ValueError("env_locked")
    _clear_pin_session(db)
    db.commit()
    release_sqlite_before_network(db)
    client = _pin_client()
    try:
        payload = client.pin_get()
    finally:
        client.close()
    data = payload.get("data") or {}
    pin = str(data.get("pin") or "")
    check = str(data.get("check") or "")
    if not pin or not check:
        raise AllDebridError("PIN_INVALID", "AllDebrid n'a pas renvoyé de code PIN.", retryable=False)
    now = _utcnow()
    expires_in = _parse_expires_in(data.get("expires_in"))
    session = {
        "pin": pin,
        "check": check,
        "user_url": data.get("user_url") or "",
        "base_url": data.get("base_url") or "",
        "started_at": now.isoformat(),
        "expires_in": expires_in,
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
    }
    _save_pin_session(db, settings, session)
    db.commit()
    return public_pin_view(session)


def cancel_pin(db: Session) -> None:
    _clear_pin_session(db)
    db.commit()


def poll_pin(db: Session, settings: Settings) -> dict[str, Any]:
    if env_api_key(settings):
        return account_snapshot(settings, db, pin_phase="idle")
    session = _load_pin_session(db, settings)
    if session is None:
        return account_snapshot(settings, db, pin_phase="expired", pin_message="Aucun code en cours.")
    if session_expired_locally(session):
        return _pin_expired_snapshot(db, settings)
    pin = str(session.get("pin") or "")
    check = str(session.get("check") or "")
    if not pin or not check:
        return _pin_expired_snapshot(db, settings)
    release_sqlite_before_network(db)
    client = _pin_client()
    try:
        payload = client.pin_check(pin, check)
    except AllDebridError as exc:
        if exc.code in _PIN_TERMINAL_ERRORS:
            return _pin_expired_snapshot(db, settings)
        logger.info("AllDebrid PIN : échec contrôle code=%s.", exc.code)
        return account_snapshot(
            settings,
            db,
            pin_phase="pending",
            pin_view=public_pin_view(session),
            pin_error=exc.message,
        )
    finally:
        client.close()
    data = payload.get("data") or {}
    if data.get("activated") and isinstance(data.get("apikey"), str) and data["apikey"].strip():
        _apply_db_api_key(db, settings, data["apikey"].strip())
        _clear_pin_session(db)
        db.commit()
        return account_snapshot(settings, db, pin_phase="idle")
    remaining = _parse_expires_in(data.get("expires_in", session.get("expires_in")))
    session["expires_in"] = remaining
    _set_expires_at_from_remaining(session, remaining)
    _save_pin_session(db, settings, session)
    db.commit()
    if session_expired_locally(session):
        return _pin_expired_snapshot(db, settings)
    return account_snapshot(settings, db, pin_phase="pending", pin_view=public_pin_view(session))


def save_manual_key(db: Session, settings: Settings, plain_key: str) -> None:
    if env_api_key(settings):
        raise ValueError("env_locked")
    key = plain_key.strip()
    if not key:
        raise ValueError("missing_key")
    release_sqlite_before_network(db)
    client = AllDebridClient(key, agent=AGENT)
    try:
        client.get_user()
    except AllDebridError:
        raise
    finally:
        client.close()
    _apply_db_api_key(db, settings, key)
    _clear_pin_session(db)
    db.commit()


def public_pin_view(session: dict[str, Any]) -> dict[str, Any]:
    user_url = session.get("user_url")
    safe_url = user_url if isinstance(user_url, str) and user_url.startswith("https://alldebrid.com/") else None
    return {
        "pin": session.get("pin"),
        "user_url": safe_url or "https://alldebrid.com/pin/",
        "expires_in": _remaining_seconds(session),
    }


def account_snapshot(
    settings: Settings,
    db: Session,
    *,
    pin_phase: PinPhase = "idle",
    pin_view: dict[str, Any] | None = None,
    pin_message: str | None = None,
    pin_error: str | None = None,
) -> dict[str, Any]:
    source = effective_source(settings, db)
    env_locked = bool(env_api_key(settings))
    connected = source != "none"
    account_notice = None if env_locked else (NEUTRAL_UNREADABLE_MESSAGE if db_key_unreadable(settings, db) else None)
    if not env_locked:
        session = _load_pin_session(db, settings)
        if session is not None and session_expired_locally(session):
            _clear_pin_session(db)
            db.commit()
            session = None
            if pin_phase in {"idle", "pending"}:
                pin_phase = "expired"
                pin_message = pin_message or PIN_EXPIRED_MESSAGE
        if pin_phase == "idle" and session is not None:
            pin_phase = "pending"
            pin_view = public_pin_view(session)
        elif pin_phase == "pending" and pin_view is None:
            if session is not None:
                pin_view = public_pin_view(session)
            else:
                pin_phase = "expired"
                pin_message = pin_message or PIN_EXPIRED_MESSAGE
    body: dict[str, Any] = {
        "connected": connected,
        "source": source,
        "env_locked": env_locked,
        "pin_phase": pin_phase,
        "pin": pin_view,
        "pin_message": pin_message,
        "pin_error": pin_error,
        "account_notice": account_notice,
    }
    if env_locked:
        body["env_notice"] = "Clé définie dans la configuration du serveur."
    return body


def response_has_secret_leak(payload: object, *, forbidden: str | None = None) -> bool:
    """Test helper: true if a response tree still carries an API key."""

    def walk(node: object) -> bool:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if lowered in {"apikey", "api_key", "authorization"} and isinstance(value, str) and value.strip():
                    if forbidden is None or value == forbidden:
                        return True
                if walk(value):
                    return True
        elif isinstance(node, list):
            return any(walk(item) for item in node)
        elif isinstance(node, str) and forbidden is not None:
            if forbidden in node:
                return True
            lowered = node.lower()
            if "bearer " in lowered and forbidden.lower() in lowered:
                return True
        elif isinstance(node, str):
            lowered = node.lower()
            if "bearer " in lowered and len(node) > 20:
                return True
        return False

    return walk(payload)
