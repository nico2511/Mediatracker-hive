"""Single entry point for the TMDB read token."""

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting
from mediatracker.security.crypto import decrypt_secret


def read_tmdb_token(db: Session, secret_key: str) -> str:
    row = db.get(Setting, "tmdb_read_token")
    if row is None or not row.value:
        return ""
    return decrypt_secret(row.value, secret_key)


__all__ = ["read_tmdb_token"]
