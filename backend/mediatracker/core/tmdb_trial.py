"""TMDB-only home rows and browse lists for the trial UI."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb.token import read_tmdb_token
from mediatracker.core.tmdb_trial_cache import cache_key, cached_tmdb_trial
from mediatracker.core.tracking import tmdb_page_url
from mediatracker.db.models import LibraryFile, Media
from mediatracker.integrations.tmdb import TmdbClient, TmdbError, TmdbHit, borrow_tmdb

_HOME_LIMIT = 20
_BROWSE_LIMIT = 60
_REGION = "FR"
_HOME_SORT = "popularity.desc"
_BROWSE_SORT_FILM_RECENT = "release_date.desc"
_BROWSE_SORT_SERIES_RECENT = "first_air_date.desc"


def _window(*, past_days: int, ahead_days: int, pivot: date | None = None) -> tuple[str, str]:
    today = pivot or date.today()
    start = today - timedelta(days=past_days)
    end = today + timedelta(days=ahead_days)
    return start.isoformat(), end.isoformat()


def _library_tmdb_ids(db: Session) -> set[int]:
    return {
        row.tmdb_id
        for row in db.scalars(select(LibraryFile).where(LibraryFile.tmdb_id.is_not(None))).all()
        if row.tmdb_id is not None
    }


def _followed_map(db: Session) -> dict[tuple[str, int], int]:
    out: dict[tuple[str, int], int] = {}
    for row in db.scalars(select(Media).where(Media.tmdb_id.is_not(None))).all():
        if row.tmdb_id is None:
            continue
        if row.followed:
            out[(row.type, row.tmdb_id)] = row.id
    return out


def _subtitle(hit: TmdbHit) -> str | None:
    if hit.year is not None:
        return str(hit.year)
    return None


def _cards(
    db: Session,
    hits: list[TmdbHit],
    *,
    followed_map: dict[tuple[str, int], int] | None = None,
    library_ids: set[int] | None = None,
    hide_in_library: bool = False,
) -> list[dict[str, Any]]:
    followed_map = followed_map if followed_map is not None else _followed_map(db)
    library_ids = library_ids if library_ids is not None else _library_tmdb_ids(db)
    found: list[dict[str, Any]] = []
    for hit in hits:
        in_library = hit.tmdb_id in library_ids
        if hide_in_library and in_library:
            continue
        media_id = followed_map.get((hit.media_type, hit.tmdb_id))
        found.append(
            {
                "tmdb_id": hit.tmdb_id,
                "media_type": hit.media_type,
                "title": hit.title,
                "year": hit.year,
                "release_date": hit.release_date.isoformat() if hit.release_date else None,
                "overview": hit.overview,
                "poster_url": hit.poster_url,
                "vote_average": hit.rating,
                "subtitle": _subtitle(hit),
                "followed": media_id is not None,
                "in_library": in_library,
                "media_id": media_id,
                "tmdb_url": tmdb_page_url(hit.media_type, hit.tmdb_id),
            }
        )
    return found


def _client(db: Session, settings: Settings) -> TmdbClient:
    token = read_tmdb_token(db, settings.secret_key)
    if not token:
        raise TmdbError("UNAUTHORIZED", "Renseignez le jeton TMDB dans les réglages.")
    client, _owned = borrow_tmdb(token, TmdbClient)
    return client


def _genre_id(genre_names: dict[int, str], label: str | None) -> str | None:
    if not label:
        return None
    target = label.strip().casefold()
    for genre_id, name in genre_names.items():
        if name.casefold() == target:
            return str(genre_id)
    return None


def home_rows(db: Session, settings: Settings) -> dict[str, list[dict[str, Any]]]:
    client = _client(db, settings)
    cinema_start, cinema_end = _window(past_days=21, ahead_days=14)
    vod_start, vod_end = _window(past_days=45, ahead_days=7)
    series_start, _ = _window(past_days=120, ahead_days=0)
    _, series_end = _window(past_days=0, ahead_days=1)
    on_air_start, on_air_end = _window(past_days=14, ahead_days=7)

    def load(section: str, loader):
        key = cache_key("tmdb-trial-home", {"section": section})
        return cached_tmdb_trial(key, loader)

    cinema_hits, _ = load(
        "cinema",
        lambda: client.discover_movies(
            page=1,
            region=_REGION,
            with_release_type="2|3",
            release_date_gte=cinema_start,
            release_date_lte=cinema_end,
            sort_by=_HOME_SORT,
        ),
    )
    streaming_hits, _ = load(
        "streaming",
        lambda: client.discover_movies(
            page=1,
            region=_REGION,
            with_release_type="4",
            watch_region=_REGION,
            release_date_gte=vod_start,
            release_date_lte=vod_end,
            sort_by=_HOME_SORT,
        ),
    )
    physical_hits, _ = load(
        "physical",
        lambda: client.discover_movies(
            page=1,
            region=_REGION,
            with_release_type="5",
            release_date_gte=vod_start,
            release_date_lte=vod_end,
            sort_by=_HOME_SORT,
        ),
    )
    new_series_hits, _ = load(
        "new_series",
        lambda: client.discover_tv(
            page=1,
            watch_region=_REGION,
            first_air_date_gte=series_start,
            first_air_date_lte=series_end,
            sort_by=_HOME_SORT,
        ),
    )
    on_air_hits, _ = load(
        "on_air",
        lambda: client.discover_tv(
            page=1,
            watch_region=_REGION,
            air_date_gte=on_air_start,
            air_date_lte=on_air_end,
            sort_by=_HOME_SORT,
        ),
    )

    followed_map = _followed_map(db)
    library_ids = _library_tmdb_ids(db)

    def pack(hits: list[TmdbHit]) -> list[dict[str, Any]]:
        return _cards(db, hits[:_HOME_LIMIT], followed_map=followed_map, library_ids=library_ids)

    return {
        "cinema_films": pack(cinema_hits),
        "streaming_films": pack(streaming_hits),
        "physical_films": pack(physical_hits),
        "new_series": pack(new_series_hits),
        "on_air_series": pack(on_air_hits),
    }


def browse_page(
    db: Session,
    settings: Settings,
    *,
    media_type: str,
    page: int,
    limit: int,
    release_kind: str | None,
    provider_id: int | None,
    genre: str | None,
    year: int | None,
    query: str | None,
    followed: bool | None,
    in_library: bool | None,
    sort_mode: str = "recent",
) -> tuple[list[dict[str, Any]], int, list[str], list[int]]:
    client = _client(db, settings)
    genre_names = client.genre_names(media_type)
    genre_id = _genre_id(genre_names, genre)
    cap = min(max(1, limit), _BROWSE_LIMIT)
    popularity = sort_mode == "popularity"
    film_sort = _HOME_SORT if popularity else _BROWSE_SORT_FILM_RECENT
    series_sort = _HOME_SORT if popularity else _BROWSE_SORT_SERIES_RECENT

    def discover_loader() -> tuple[list[TmdbHit], int]:
        if query and query.strip():
            return client.search_page(query.strip(), media_type, page=page)
        if media_type == "film":
            release_types = {
                "cinema": "2|3",
                "streaming": "4",
                "physical": "5",
            }.get(release_kind or "streaming", "4")
            start, end = _window(past_days=60, ahead_days=14)
            use_watch = release_kind in {None, "streaming"}
            return client.discover_movies(
                page=page,
                region=_REGION,
                with_release_type=release_types,
                watch_region=_REGION if use_watch else None,
                with_watch_providers=str(provider_id) if provider_id is not None else None,
                release_date_gte=start,
                release_date_lte=end,
                primary_release_year=year,
                with_genres=genre_id,
                sort_by=film_sort,
            )
        if release_kind == "on_air":
            air_start, air_end = _window(past_days=14, ahead_days=7)
            return client.discover_tv(
                page=page,
                watch_region=_REGION,
                air_date_gte=air_start,
                air_date_lte=air_end,
                with_watch_providers=str(provider_id) if provider_id is not None else None,
                with_genres=genre_id,
                sort_by=series_sort,
            )
        start, end = _window(past_days=180, ahead_days=1)
        return client.discover_tv(
            page=page,
            watch_region=_REGION,
            first_air_date_gte=start,
            first_air_date_lte=end,
            with_watch_providers=str(provider_id) if provider_id is not None else None,
            with_genres=genre_id,
            sort_by=series_sort,
        )

    key = cache_key(
        "tmdb-trial-browse",
        {
            "media_type": media_type,
            "page": page,
            "limit": cap,
            "release_kind": release_kind,
            "provider_id": provider_id,
            "genre": genre,
            "year": year,
            "query": (query or "").strip(),
            "sort_mode": sort_mode,
        },
    )
    hits, total_pages = cached_tmdb_trial(key, discover_loader)
    hide_library = in_library is False
    cards = _cards(db, hits[:cap], hide_in_library=hide_library)
    if followed is True:
        cards = [row for row in cards if row["followed"]]
    elif followed is False:
        cards = [row for row in cards if not row["followed"]]
    if in_library is True:
        cards = [row for row in cards if row["in_library"]]
    elif in_library is False:
        cards = [row for row in cards if not row["in_library"]]
    genres = sorted(genre_names.values(), key=str.casefold)
    years = list(range(date.today().year, date.today().year - 30, -1))
    approx_total = total_pages * cap
    return cards, approx_total, genres, years


def providers_fr(db: Session, settings: Settings) -> list[dict[str, int | str]]:
    client = _client(db, settings)

    def loader() -> list[dict[str, int | str]]:
        return [{"id": pid, "name": name} for pid, name in client.watch_providers_movie_fr()]

    key = cache_key("tmdb-trial-providers", {"region": _REGION})
    return cached_tmdb_trial(key, loader)
