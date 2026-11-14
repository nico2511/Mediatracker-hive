"""Apply a fetched TMDB title onto a media row."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.tmdb import TmdbTitle
from mediatracker.integrations.tmdb_enrichment import apply_tmdb_enrichment


def owner_for_tmdb(
    db: Session,
    tmdb_id: int,
    media_type: str,
    *,
    exclude_media_id: int | None = None,
) -> Media | None:
    stmt = select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type)
    if exclude_media_id is not None:
        stmt = stmt.where(Media.id != exclude_media_id)
    return db.scalar(stmt)


def apply_tmdb(
    db: Session,
    row: Media,
    fetched: TmdbTitle,
    *,
    imdb_id: str | None,
    moment: datetime,
) -> list[MediaItem]:
    row.title = fetched.title or row.title
    row.original_title = fetched.original_title or row.original_title
    row.year = fetched.year or row.year
    row.overview = fetched.overview
    row.poster_url = fetched.poster_url
    row.backdrop_url = fetched.backdrop_url
    row.series_status = fetched.series_status or row.series_status
    if imdb_id:
        row.imdb_id = imdb_id
    elif fetched.imdb_id and not row.imdb_id:
        row.imdb_id = fetched.imdb_id
    row.tmdb_refreshed_at = moment
    row.tmdb_identify_status = None
    row.tmdb_identify_candidates = None
    apply_tmdb_enrichment(row, fetched)
    from mediatracker.library.repair import apply_tmdb_metadata

    episodes = apply_tmdb_metadata(db, row, fetched)
    if row.type == "film":
        from mediatracker.core.film_availability import apply_film_from_tmdb

        apply_film_from_tmdb(db, row, fetched)
    elif row.type == "series":
        from mediatracker.integrations.tmdb_media import fill_next_episode

        fill_next_episode(row, episodes)
    return episodes
