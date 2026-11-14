"""Fetch a TMDB title for identification and binding."""

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.integrations.tmdb import TmdbError, TmdbTitle, shared_tmdb


def fetch_title(db: Session, settings: Settings, tmdb_id: int, media_type: str) -> TmdbTitle | None:
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return None
    try:
        return shared_tmdb(token).fetch(tmdb_id, media_type)
    except TmdbError:
        return None
