"""Open a shared TMDB client from stored settings."""

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.integrations.tmdb import TmdbClient, shared_tmdb


def open_client(db: Session, settings: Settings) -> tuple[TmdbClient | None, bool]:
    """Return ``(client, owns_client)``. The shared client does not need ``close()``."""
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return None, False
    return shared_tmdb(token), False
