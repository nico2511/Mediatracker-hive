"""Open a shared TMDB client from stored settings."""

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.core.tmdb.catalog_settings import read_tmdb_catalog_settings
from mediatracker.integrations.tmdb import TmdbClient, borrow_tmdb, shared_tmdb


def open_client(db: Session, settings: Settings) -> tuple[TmdbClient | None, bool]:
    """Return ``(client, owns_client)``. The shared client does not need ``close()``."""
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return None, False
    catalog = read_tmdb_catalog_settings(db)
    return shared_tmdb(token, language=catalog.display_language), False


def borrow_client(
    db: Session,
    settings: Settings,
    factory: type[TmdbClient] = TmdbClient,
) -> tuple[TmdbClient | None, bool]:
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return None, False
    catalog = read_tmdb_catalog_settings(db)
    return borrow_tmdb(token, factory, language=catalog.display_language)
