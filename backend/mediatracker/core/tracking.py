"""Pure tracking figures for a series fiche and the home dashboard."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class EpisodeView:
    season: int | None
    episode: int | None
    title: str | None
    air_date: date | None
    downloaded: bool
    watched: bool
    source_available: bool


@dataclass(frozen=True)
class NextEpisode:
    season: int | None
    episode: int | None
    title: str | None
    air_date: date | None


@dataclass(frozen=True)
class Progress:
    watched: int
    total: int


def tmdb_page_url(media_type: str, tmdb_id: int | None) -> str | None:
    if not tmdb_id:
        return None
    kind = "tv" if media_type == "series" else "movie"
    return f"https://www.themoviedb.org/{kind}/{tmdb_id}"


def progress_of(episodes: list[EpisodeView]) -> Progress:
    return Progress(watched=sum(1 for episode in episodes if episode.watched), total=len(episodes))


def available_not_downloaded(episodes: list[EpisodeView]) -> int:
    return sum(1 for episode in episodes if episode.source_available and not episode.downloaded)


def resolve_next_episode(
    *,
    media_type: str,
    season: int | None,
    episode: int | None,
    title: str | None,
    air_date: date | None,
    episodes: list[EpisodeView],
    today: date | None = None,
) -> NextEpisode | None:
    """Next *upcoming* episode of a series, or None.

    A stored value is shown only when its air date is today or later (or unknown for a manual
    entry). Otherwise the earliest unwatched episode airing today or later is chosen, specials
    (season 0) only when nothing else is announced. Past episodes are never shown as "next".
    """
    if media_type != "series":
        return None
    day = today or date.today()
    future = [
        item
        for item in episodes
        if not item.watched and item.air_date is not None and item.air_date >= day and item.episode is not None
    ]
    regular = [item for item in future if item.season != 0]
    if season is not None or episode is not None or air_date is not None:
        matched = _match(episodes, season, episode)
        effective = air_date or (matched.air_date if matched else None)
        upcoming = effective is None or effective >= day
        special_hidden = season == 0 and bool(regular)
        if upcoming and not special_hidden:
            return NextEpisode(
                season=season,
                episode=episode,
                title=title or (matched.title if matched else None),
                air_date=effective,
            )
    pool = regular or future
    if not pool:
        return None
    pool.sort(key=lambda item: (item.air_date or date.max, item.season or 0, item.episode or 0))
    chosen = pool[0]
    return NextEpisode(
        season=chosen.season,
        episode=chosen.episode,
        title=chosen.title,
        air_date=chosen.air_date,
    )


def _match(episodes: list[EpisodeView], season: int | None, episode: int | None) -> EpisodeView | None:
    for item in episodes:
        if item.season == season and item.episode == episode:
            return item
    return None
