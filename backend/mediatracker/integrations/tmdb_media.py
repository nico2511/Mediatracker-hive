"""Apply a TMDB title onto local media rows. The HTTP client stays in tmdb.py."""

from datetime import date

from sqlalchemy.orm import Session

from mediatracker.db.models import Media, MediaItem, MediaSeason
from mediatracker.integrations.tmdb import TmdbTitle


def store_tmdb_episodes(db: Session, row: Media, fetched: TmdbTitle) -> list[MediaItem]:
    items: list[MediaItem] = []
    if row.type == "film":
        item = MediaItem(
            media_id=row.id,
            title=row.title,
            air_date=fetched.release_date,
            state="missing",
        )
        db.add(item)
        return [item]
    for season in fetched.seasons:
        db.add(
            MediaSeason(
                media_id=row.id,
                number=season.number,
                name=season.name,
                overview=season.overview,
                air_date=season.air_date,
                episode_count=season.episode_count,
                tmdb_id=season.tmdb_id,
            )
        )
        for episode in season.episodes:
            item = MediaItem(
                media_id=row.id,
                season=episode.season,
                episode=episode.episode,
                title=episode.title,
                air_date=episode.air_date,
                still_url=episode.still_url,
                tmdb_episode_id=episode.tmdb_episode_id,
                state="missing",
            )
            db.add(item)
            items.append(item)
    return items


def pick_upcoming_episode(episodes: list[MediaItem], today: date | None = None) -> MediaItem | None:
    """Earliest episode airing today or later; specials (season 0) only when nothing else is announced."""
    day = today or date.today()
    future = [
        item
        for item in episodes
        if item.air_date is not None and item.air_date >= day and item.episode is not None
    ]
    regular = [item for item in future if item.season != 0]
    pool = regular or future
    if not pool:
        return None
    pool.sort(key=lambda item: (item.air_date or date.max, item.season or 0, item.episode or 0))
    return pool[0]


def fill_next_episode(row: Media, episodes: list[MediaItem], *, today: date | None = None) -> None:
    day = today or date.today()
    if row.type == "film":
        if row.next_air_date is not None and row.next_episode_number is not None:
            return
        pending = [item for item in episodes if item.air_date is not None]
        if not pending:
            return
        future = [item for item in pending if item.air_date and item.air_date >= day]
        pool = future or pending
        pool.sort(key=lambda item: (item.air_date or date.max, item.season or 0, item.episode or 0))
        _apply_next(row, pool[0])
        return
    stored_upcoming = (
        row.next_air_date is not None
        and row.next_episode_number is not None
        and row.next_air_date >= day
        and row.next_episode_season != 0
    )
    if stored_upcoming:
        return
    chosen = pick_upcoming_episode(episodes, day)
    if chosen is not None:
        _apply_next(row, chosen)
    # Nothing upcoming: an already stored past value is left as is (source polling may still use a
    # just-aired episode); the API hides any next episode whose air date is in the past.


def _apply_next(row: Media, chosen: MediaItem) -> None:
    row.next_episode_season = chosen.season
    row.next_episode_number = chosen.episode
    row.next_episode_title = chosen.title
    row.next_air_date = chosen.air_date
