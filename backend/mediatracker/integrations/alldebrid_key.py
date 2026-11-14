"""Single source for the effective AllDebrid API key (env beats database)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Provider
from mediatracker.security.crypto import decrypt_secret, is_encrypted

NEUTRAL_UNREADABLE_MESSAGE = (
    "La clé AllDebrid enregistrée n'est plus lisible avec la configuration actuelle du serveur."
)


def env_api_key(settings: Settings) -> str:
    return (settings.alldebrid_api_key or "").strip()


def provider_row(db: Session) -> Provider | None:
    return db.scalar(select(Provider).where(Provider.kind == "debrid", Provider.type == "alldebrid"))


def _stored_ciphertext(db: Session) -> str:
    row = provider_row(db)
    if row is None:
        return ""
    stored = (row.config or {}).get("api_key")
    return stored if isinstance(stored, str) else ""


def db_key_unreadable(settings: Settings, db: Session) -> bool:
    if env_api_key(settings):
        return False
    stored = _stored_ciphertext(db)
    if not stored or not is_encrypted(stored):
        return False
    try:
        decrypt_secret(stored, settings.secret_key)
    except ValueError:
        return True
    return False


def effective_alldebrid_key(settings: Settings, db: Session) -> str:
    env = env_api_key(settings)
    if env:
        return env
    row = provider_row(db)
    if row is None or not row.enabled:
        return ""
    stored = _stored_ciphertext(db)
    if not stored:
        return ""
    if is_encrypted(stored):
        try:
            return decrypt_secret(stored, settings.secret_key).strip()
        except ValueError:
            return ""
    return stored.strip()


def merge_alldebrid_api_key(plain: dict, settings: Settings, db: Session) -> dict:
    merged = dict(plain)
    merged["api_key"] = effective_alldebrid_key(settings, db)
    return merged
