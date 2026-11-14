"""Resolve IMDb ids from TMDB for episode sources."""

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import Media


def ensure_imdb(db: Session, media: Media, settings: Settings) -> str | None:
    current = (media.imdb_id or "").strip()
    if current.startswith("tt"):
        return current
    if media.tmdb_id is None:
        return None
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return None
    from mediatracker.core.tmdb.fetch import fetch_title

    fetched = fetch_title(db, settings, media.tmdb_id, media.type)
    if fetched is None:
        return None
    imdb = (fetched.imdb_id or "").strip()
    if imdb.startswith("tt"):
        media.imdb_id = imdb
        return imdb
    return None
