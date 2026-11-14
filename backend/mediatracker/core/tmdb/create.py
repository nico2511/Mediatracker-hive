"""Create a local fiche from TMDB metadata."""

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.tmdb import TmdbTitle
from mediatracker.integrations.tmdb_enrichment import apply_tmdb_enrichment


def create_from_tmdb(
    db: Session,
    *,
    media_type: str,
    tmdb_id: int | None,
    fetched: TmdbTitle | None,
    now: datetime,
    title: str | None = None,
    year: int | None = None,
    imdb_id: str | None = None,
    followed: bool = True,
    follow_origin: str = "user",
    **fields,
) -> tuple[Media, list[MediaItem]]:
    """Insert a ``Media`` row and episode slots from TMDB (same shape as ``POST /media``)."""
    row = Media(
        type=media_type,
        title=title or (fetched.title if fetched else "") or "",
        year=fetched.year if fetched and fetched.year else year,
        tmdb_id=tmdb_id if tmdb_id is not None else (fetched.tmdb_id if fetched else None),
        overview=fetched.overview if fetched else fields.pop("overview", None),
        poster_url=fetched.poster_url if fetched else fields.pop("poster_url", None),
        backdrop_url=fetched.backdrop_url if fetched else None,
        original_title=fetched.original_title if fetched else None,
        imdb_id=imdb_id or (fetched.imdb_id if fetched else None),
        followed=followed,
        follow_origin=follow_origin,
        series_status=fields.pop("series_status", fetched.series_status if fetched else None),
        next_air_date=fields.pop("next_air_date", fetched.release_date if fetched else None),
        created_at=now,
        **fields,
    )
    db.add(row)
    db.flush()
    if fetched is not None:
        apply_tmdb_enrichment(row, fetched)
    episodes: list[MediaItem] = []
    if fetched is not None:
        from mediatracker.integrations.tmdb_media import store_tmdb_episodes

        episodes = store_tmdb_episodes(db, row, fetched)
        db.flush()
        if row.type == "film":
            from mediatracker.core.film_availability import apply_film_from_tmdb

            apply_film_from_tmdb(db, row, fetched)
        elif row.type == "series":
            from mediatracker.integrations.tmdb_media import fill_next_episode

            fill_next_episode(row, episodes)
    row.tmdb_refreshed_at = now
    return row, episodes
