"""TMDB read API. Used to follow a title before any source lists it."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

_URL = re.compile(r"themoviedb\.org/(?:[a-z]{2}/)?(tv|movie)/(\d+)", re.IGNORECASE)
_STATUS = {
    "Returning Series": "returning",
    "Ended": "ended",
    "Canceled": "canceled",
    "Planned": "planned",
    "In Production": "in_production",
}
POSTER_ROOT = "https://image.tmdb.org/t/p/w500"
THUMB_ROOT = "https://image.tmdb.org/t/p/w92"
BACKDROP_ROOT = "https://image.tmdb.org/t/p/w1280"
WATCH_MONETIZATION_TYPES_FR = "flatrate|free|ads|rent|buy"


class TmdbError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class TmdbRef:
    tmdb_id: int
    media_type: str | None


@dataclass(frozen=True)
class TmdbSeasonBrief:
    number: int
    name: str | None
    episode_count: int | None


@dataclass(frozen=True)
class TmdbHit:
    tmdb_id: int
    media_type: str
    title: str
    year: int | None
    overview: str | None
    poster_url: str | None
    backdrop_url: str | None = None
    rating: float | None = None
    genre_ids: tuple[int, ...] = ()
    original_title: str | None = None
    season_count: int | None = None
    episode_count: int | None = None
    runtime: int | None = None
    seasons: tuple[TmdbSeasonBrief, ...] = ()


@dataclass(frozen=True)
class TmdbEpisode:
    season: int
    episode: int
    title: str | None
    air_date: date | None
    tmdb_episode_id: int | None
    still_url: str | None = None


@dataclass(frozen=True)
class TmdbSeason:
    number: int
    name: str | None
    overview: str | None
    air_date: date | None
    episode_count: int | None
    tmdb_id: int | None
    episodes: tuple[TmdbEpisode, ...]


@dataclass(frozen=True)
class DigitalReleaseHit:
    """One movie or series whose date falls in the requested window."""

    tmdb_id: int
    title: str
    poster_url: str | None
    release_date: date
    overview: str | None = None


@dataclass(frozen=True)
class TmdbTitle:
    tmdb_id: int
    media_type: str
    title: str
    year: int | None
    overview: str | None
    poster_url: str | None
    series_status: str | None
    release_date: date | None
    seasons: tuple[TmdbSeason, ...]
    backdrop_url: str | None = None
    original_title: str | None = None
    release_dates: dict[str, Any] | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    genres: tuple[str, ...] = ()
    runtime_minutes: int | None = None
    original_language: str | None = None
    collection_id: int | None = None
    collection_name: str | None = None
    watch_providers_fr: tuple[str, ...] = ()
    release_by_region: dict[str, dict[str, str | None]] | None = None
    imdb_id: str | None = None


def parse_tmdb_url(url: str) -> TmdbRef:
    match = _URL.search(url.strip())
    if match is None:
        raise TmdbError("BAD_URL", "L'URL TMDB doit ressembler à https://www.themoviedb.org/tv/900101.")
    kind, raw_id = match.group(1).lower(), int(match.group(2))
    return TmdbRef(tmdb_id=raw_id, media_type="series" if kind == "tv" else "film")


def poster_url(path: str | None, *, thumb: bool = False) -> str | None:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        if thumb and "/t/p/w500/" in path:
            return path.replace("/t/p/w500/", "/t/p/w92/")
        return path
    root = THUMB_ROOT if thumb else POSTER_ROOT
    return f"{root}{path if path.startswith('/') else '/' + path}"


def backdrop_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"{BACKDROP_ROOT}{path}"


def still_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{BACKDROP_ROOT}{path if path.startswith('/') else '/' + path}"


def _original(original: object, localized: object) -> str | None:
    if not isinstance(original, str):
        return None
    text = original.strip()
    if not text:
        return None
    if isinstance(localized, str) and text.casefold() == localized.strip().casefold():
        return None
    return text


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _hit(
    row: dict,
    media_type: str,
    title: str,
    aired: str | None,
    rating: float | None,
    genre_ids: tuple[int, ...],
    *,
    original: object = None,
) -> TmdbHit:
    localized = row.get("name") if media_type == "series" else row.get("title")
    return TmdbHit(
        tmdb_id=int(row["id"]),
        media_type=media_type,
        title=title,
        year=_year(aired),
        overview=row.get("overview") or None,
        poster_url=poster_url(row.get("poster_path")),
        backdrop_url=backdrop_url(row.get("backdrop_path")),
        rating=rating,
        genre_ids=genre_ids,
        original_title=_original(
            original if original is not None else row.get("original_name") or row.get("original_title"),
            localized or title,
        ),
        season_count=_count(row.get("number_of_seasons")),
        episode_count=_count(row.get("number_of_episodes")),
        runtime=_count(row.get("runtime")),
    )


def _discover_hits(payload: dict[str, Any], media_type: str) -> list[TmdbHit]:
    hits: list[TmdbHit] = []
    for row in payload.get("results") or []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        if media_type == "series":
            title = row.get("name") or row.get("original_name")
            aired = row.get("first_air_date")
            original = row.get("original_name")
        else:
            title = row.get("title") or row.get("original_title")
            aired = row.get("release_date")
            original = row.get("original_title")
        if not title:
            continue
        raw_ids = row.get("genre_ids") or []
        genre_ids = tuple(int(item) for item in raw_ids if isinstance(item, int))
        vote = row.get("vote_average")
        rating = float(vote) if isinstance(vote, int | float) and not isinstance(vote, bool) else None
        hits.append(
            _hit(
                row,
                media_type,
                str(title),
                aired if isinstance(aired, str) else None,
                rating,
                genre_ids,
                original=original,
            )
        )
    return hits


def _season_briefs(row: dict) -> tuple[TmdbSeasonBrief, ...]:
    """Season numbers from the series detail. Episode pages stay for a full fetch."""
    briefs: list[TmdbSeasonBrief] = []
    for season in row.get("seasons") or []:
        if not isinstance(season, dict):
            continue
        number = season.get("season_number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 0 or number > 40:
            continue
        name = season.get("name") if isinstance(season.get("name"), str) else None
        count = season.get("episode_count")
        episode_count = count if isinstance(count, int) and not isinstance(count, bool) else None
        briefs.append(TmdbSeasonBrief(number=number, name=name, episode_count=episode_count))
    briefs.sort(key=lambda item: item.number)
    return tuple(briefs)


def _year(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    head = value[:4]
    return int(head) if head.isdigit() else None


_SHARED: dict[str, TmdbClient] = {}
_RETRY_CAP_SECONDS = 30.0


def shared_tmdb(token: str) -> TmdbClient:
    """One client per token for the process. Replaced clients are closed.

    A monkeypatched client class is not reused, so tests stay isolated.
    """
    current = _SHARED.get(token)
    if (
        current is not None
        and type(current) is TmdbClient
        and not current._client.is_closed
    ):
        return current
    client = TmdbClient(token)
    previous = _SHARED.get(token)
    if previous is not None and previous is not client:
        close = getattr(previous, "close", None)
        if close is not None:
            close()
    _SHARED[token] = client
    return client


def borrow_tmdb(token: str, factory: Callable[..., TmdbClient]) -> tuple[TmdbClient, bool]:
    """Return ``(client, owned)``. The shared client is not closed by the caller.

    ``factory`` is the name tests replace (``media.TmdbClient`` and the like).
    A substitute is created for that call and the caller closes it.
    """
    if factory is TmdbClient:
        return shared_tmdb(token), False
    return factory(token), True


def close_shared_tmdb() -> None:
    for client in _SHARED.values():
        client.close()
    _SHARED.clear()


def _retry_after(value: str | None) -> float:
    if not value:
        return 1.0
    try:
        return min(_RETRY_CAP_SECONDS, max(0.0, float(value)))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return 1.0
    delay = moment.timestamp() - time.time()
    return min(_RETRY_CAP_SECONDS, max(0.0, delay))


def _overview(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    return text or None


def _day(value: str | None) -> date | None:
    if not value or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _detail_enrichment(
    row: dict[str, Any],
    *,
    media_type: str,
    release_dates: dict[str, Any] | None,
) -> dict[str, Any]:
    from mediatracker.integrations.tmdb_enrichment import detail_enrichment_from_row

    return detail_enrichment_from_row(row, media_type=media_type, release_dates=release_dates)


class TmdbClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        request_timeout: float = 20.0,
    ) -> None:
        from mediatracker.core.outbound_http import borrow_http_client

        self._client, self._owns_client = borrow_http_client(
            ("tmdb", token),
            httpx.Client,
            transport=transport,
            base_url="https://api.themoviedb.org/3",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"language": "fr-FR"},
            timeout=request_timeout,
        )
        self._sleeper = sleeper

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def ping(self) -> None:
        """One authenticated read. The token stays in the header and is not logged."""
        self._get("/configuration")

    def search(self, query: str, media_type: str) -> list[TmdbHit]:
        path = "/search/tv" if media_type == "series" else "/search/movie"
        payload = self._get(path, {"query": query})
        hits: list[TmdbHit] = []
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            title = row.get("name") if media_type == "series" else row.get("title")
            if not title:
                continue
            aired = row.get("first_air_date") if media_type == "series" else row.get("release_date")
            raw_ids = row.get("genre_ids") or []
            genre_ids = tuple(int(item) for item in raw_ids if isinstance(item, int))
            vote = row.get("vote_average")
            rating = float(vote) if isinstance(vote, int | float) and not isinstance(vote, bool) else None
            hits.append(_hit(row, media_type, str(title), aired if isinstance(aired, str) else None, rating, genre_ids))
        return hits

    def search_multi(self, query: str) -> list[TmdbHit]:
        """TMDB ``/search/multi`` in the client's language (fr-FR). People are ignored."""
        payload = self._get("/search/multi", {"query": query})
        hits: list[TmdbHit] = []
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            kind = row.get("media_type")
            if kind == "tv":
                media_type = "series"
                title = row.get("name")
                aired = row.get("first_air_date")
            elif kind == "movie":
                media_type = "film"
                title = row.get("title")
                aired = row.get("release_date")
            else:
                continue
            if not title:
                continue
            hits.append(_hit(row, media_type, str(title), aired if isinstance(aired, str) else None, None, ()))
        return hits

    def genre_names(self, media_type: str) -> dict[int, str]:
        """French genre labels. One call per type; the catalog resolves ids from search hits."""
        path = "/genre/tv/list" if media_type == "series" else "/genre/movie/list"
        payload = self._get(path)
        names: dict[int, str] = {}
        for row in payload.get("genres") or []:
            if isinstance(row, dict) and isinstance(row.get("id"), int) and row.get("name"):
                names[int(row["id"])] = str(row["name"])
        return names

    def glance(self, tmdb_id: int, media_type: str) -> TmdbHit:
        """One detail call, without walking every season. Used to describe a candidate."""
        path = f"/movie/{tmdb_id}" if media_type == "film" else f"/tv/{tmdb_id}"
        row = self._get(path)
        if media_type == "film":
            title = str(row.get("title") or row.get("original_title") or "Film")
            aired = row.get("release_date")
            original = row.get("original_title")
        else:
            title = str(row.get("name") or row.get("original_name") or "Série")
            aired = row.get("first_air_date")
            original = row.get("original_name")
        hit = _hit(row, media_type, title, aired if isinstance(aired, str) else None, None, (), original=original)
        if media_type != "series":
            return hit
        return replace(hit, seasons=_season_briefs(row))

    def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
        if media_type == "film":
            return self._movie(tmdb_id)
        return self._series(tmdb_id)

    def digital_releases(self, *, start: date, end: date, region: str = "FR") -> list[DigitalReleaseHit]:
        """Movies with a digital release in ``region`` between ``start`` and ``end``.

        TMDB release type 4 is digital. The result ``release_date`` is the one
        that matched the region filter.
        """
        payload = self._get(
            "/discover/movie",
            {
                "region": region,
                "with_release_type": "4",
                "release_date.gte": start.isoformat(),
                "release_date.lte": end.isoformat(),
                "sort_by": "popularity.desc",
                "include_adult": "false",
            },
        )
        found: list[DigitalReleaseHit] = []
        for row in payload.get("results") or []:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                continue
            title = row.get("title") or row.get("original_title")
            released = _day(row.get("release_date") if isinstance(row.get("release_date"), str) else None)
            if not title or released is None:
                continue
            found.append(
                DigitalReleaseHit(
                    tmdb_id=int(row["id"]),
                    title=str(title),
                    poster_url=poster_url(row.get("poster_path")),
                    release_date=released,
                    overview=_overview(row.get("overview")),
                )
            )
        return found

    def airing_series(self, *, start: date, end: date, region: str = "FR") -> list[DigitalReleaseHit]:
        """Series with an episode airing in ``region`` between ``start`` and ``end``.

        New shows and new seasons both surface here: TMDB filters on the episode
        air date, and ``watch_region`` keeps the window to France.
        """
        payload = self._get(
            "/discover/tv",
            {
                "watch_region": region,
                "air_date.gte": start.isoformat(),
                "air_date.lte": end.isoformat(),
                "sort_by": "popularity.desc",
                "include_adult": "false",
                "with_watch_monetization_types": WATCH_MONETIZATION_TYPES_FR,
            },
        )
        found: list[DigitalReleaseHit] = []
        for row in payload.get("results") or []:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                continue
            title = row.get("name") or row.get("original_name")
            aired = _day(row.get("first_air_date") if isinstance(row.get("first_air_date"), str) else None)
            if not title:
                continue
            # An older show with a new season still matches the air-date filter.
            # The premiere date is what TMDB returns; the window start stands in when it is missing.
            found.append(
                DigitalReleaseHit(
                    tmdb_id=int(row["id"]),
                    title=str(title),
                    poster_url=poster_url(row.get("poster_path")),
                    release_date=aired or start,
                    overview=_overview(row.get("overview")),
                )
            )
        return found

    def discover_movies(
        self,
        *,
        page: int = 1,
        region: str = "FR",
        with_release_type: str,
        watch_region: str | None = None,
        with_watch_providers: str | None = None,
        with_watch_monetization_types: str | None = None,
        release_date_gte: str | None = None,
        release_date_lte: str | None = None,
        primary_release_year: int | None = None,
        with_genres: str | None = None,
        sort_by: str = "popularity.desc",
    ) -> tuple[list[TmdbHit], int]:
        params: dict[str, str] = {
            "region": region,
            "with_release_type": with_release_type,
            "page": str(max(1, page)),
            "sort_by": sort_by,
            "include_adult": "false",
        }
        if watch_region:
            params["watch_region"] = watch_region
            params["with_watch_monetization_types"] = (
                with_watch_monetization_types or WATCH_MONETIZATION_TYPES_FR
            )
        elif with_watch_monetization_types:
            params["with_watch_monetization_types"] = with_watch_monetization_types
        if with_watch_providers:
            params["with_watch_providers"] = with_watch_providers
        if release_date_gte:
            params["release_date.gte"] = release_date_gte
        if release_date_lte:
            params["release_date.lte"] = release_date_lte
        if primary_release_year is not None:
            params["primary_release_year"] = str(primary_release_year)
        if with_genres:
            params["with_genres"] = with_genres
        payload = self._get("/discover/movie", params)
        return _discover_hits(payload, "film"), int(payload.get("total_pages") or 1)

    def discover_tv(
        self,
        *,
        page: int = 1,
        watch_region: str = "FR",
        first_air_date_gte: str | None = None,
        first_air_date_lte: str | None = None,
        air_date_gte: str | None = None,
        air_date_lte: str | None = None,
        with_watch_providers: str | None = None,
        with_watch_monetization_types: str | None = None,
        with_genres: str | None = None,
        sort_by: str = "popularity.desc",
    ) -> tuple[list[TmdbHit], int]:
        params: dict[str, str] = {
            "watch_region": watch_region,
            "page": str(max(1, page)),
            "sort_by": sort_by,
            "include_adult": "false",
            "with_watch_monetization_types": with_watch_monetization_types or WATCH_MONETIZATION_TYPES_FR,
        }
        if first_air_date_gte:
            params["first_air_date.gte"] = first_air_date_gte
        if first_air_date_lte:
            params["first_air_date.lte"] = first_air_date_lte
        if air_date_gte:
            params["air_date.gte"] = air_date_gte
        if air_date_lte:
            params["air_date.lte"] = air_date_lte
        if with_watch_providers:
            params["with_watch_providers"] = with_watch_providers
        if with_genres:
            params["with_genres"] = with_genres
        payload = self._get("/discover/tv", params)
        return _discover_hits(payload, "series"), int(payload.get("total_pages") or 1)

    def on_the_air(self, *, page: int = 1, watch_region: str = "FR") -> tuple[list[TmdbHit], int]:
        payload = self._get(
            "/tv/on_the_air",
            {"page": str(max(1, page)), "watch_region": watch_region},
        )
        return _discover_hits(payload, "series"), int(payload.get("total_pages") or 1)

    def search_page(self, query: str, media_type: str, *, page: int = 1) -> tuple[list[TmdbHit], int]:
        path = "/search/tv" if media_type == "series" else "/search/movie"
        payload = self._get(path, {"query": query, "page": str(max(1, page))})
        hits: list[TmdbHit] = []
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            title = row.get("name") if media_type == "series" else row.get("title")
            if not title:
                continue
            aired = row.get("first_air_date") if media_type == "series" else row.get("release_date")
            raw_ids = row.get("genre_ids") or []
            genre_ids = tuple(int(item) for item in raw_ids if isinstance(item, int))
            vote = row.get("vote_average")
            rating = float(vote) if isinstance(vote, int | float) and not isinstance(vote, bool) else None
            hits.append(_hit(row, media_type, str(title), aired if isinstance(aired, str) else None, rating, genre_ids))
        return hits, int(payload.get("total_pages") or 1)

    def watch_providers_movie_fr(self) -> list[tuple[int, str]]:
        payload = self._get("/watch/providers/movie", {"watch_region": "FR"})
        block = payload.get("results") or {}
        fr = block.get("FR") if isinstance(block, dict) else None
        if not isinstance(fr, dict):
            return []
        seen: dict[int, str] = {}
        for key in ("flatrate", "rent", "buy"):
            for item in fr.get(key) or []:
                if not isinstance(item, dict):
                    continue
                provider_id = item.get("provider_id")
                name = item.get("provider_name")
                if isinstance(provider_id, int) and isinstance(name, str) and name.strip():
                    seen[provider_id] = name.strip()
        return sorted(seen.items(), key=lambda pair: pair[1].casefold())

    def find_external(self, external_id: str, source: str) -> tuple[int | None, str | None]:
        """Resolve an IMDb or TVDB id. ``source`` is ``imdb_id`` or ``tvdb_id``.

        Returns ``(tmdb_id, film|series)``. An empty result is ``(None, None)``.
        """
        if source not in {"imdb_id", "tvdb_id"}:
            raise TmdbError("BAD_RESPONSE", "Source externe inconnue.")
        external = external_id.strip()
        if not external:
            return None, None
        payload = self._get(f"/find/{external}", {"external_source": source})
        movies = payload.get("movie_results") or []
        shows = payload.get("tv_results") or []
        if movies and isinstance(movies[0], dict) and isinstance(movies[0].get("id"), int):
            return int(movies[0]["id"]), "film"
        if shows and isinstance(shows[0], dict) and isinstance(shows[0].get("id"), int):
            return int(shows[0]["id"]), "series"
        return None, None

    def external_imdb(self, tmdb_id: int, media_type: str) -> str | None:
        """IMDb id (``tt…``) from TMDB external_ids. Missing or refused stays empty."""
        path = f"/movie/{tmdb_id}/external_ids" if media_type == "film" else f"/tv/{tmdb_id}/external_ids"
        try:
            row = self._get(path)
        except TmdbError:
            return None
        imdb = row.get("imdb_id")
        if isinstance(imdb, str) and imdb.startswith("tt"):
            return imdb
        return None

    def _movie(self, tmdb_id: int) -> TmdbTitle:
        row = self._get(
            f"/movie/{tmdb_id}",
            {"append_to_response": "release_dates,watch/providers,external_ids"},
        )
        released = _day(row.get("release_date"))
        release_dates = row.get("release_dates") if isinstance(row.get("release_dates"), dict) else None
        enrichment = _detail_enrichment(row, media_type="film", release_dates=release_dates)
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type="film",
            title=str(row.get("title") or row.get("original_title") or "Film"),
            year=released.year if released else _year(row.get("release_date")),
            overview=row.get("overview") or None,
            poster_url=poster_url(row.get("poster_path")),
            series_status=None,
            release_date=released,
            seasons=(),
            backdrop_url=backdrop_url(row.get("backdrop_path")),
            original_title=_original(row.get("original_title"), row.get("title")),
            release_dates=release_dates,
            **enrichment,
        )

    def _series(self, tmdb_id: int) -> TmdbTitle:
        row = self._get(f"/tv/{tmdb_id}", {"append_to_response": "watch/providers,external_ids"})
        seasons: list[TmdbSeason] = []
        for season in row.get("seasons") or []:
            if not isinstance(season, dict):
                continue
            number = season.get("season_number")
            # Season 0 is TMDB specials (Jellyfin Season 00).
            if not isinstance(number, int) or number < 0 or number > 40:
                continue
            seasons.append(self._season(tmdb_id, number, season))
        premiered = _day(row.get("first_air_date"))
        status = _STATUS.get(str(row.get("status") or ""), "unknown")
        enrichment = _detail_enrichment(row, media_type="series", release_dates=None)
        return TmdbTitle(
            tmdb_id=tmdb_id,
            media_type="series",
            title=str(row.get("name") or row.get("original_name") or "Série"),
            year=premiered.year if premiered else _year(row.get("first_air_date")),
            overview=row.get("overview") or None,
            poster_url=poster_url(row.get("poster_path")),
            series_status=status,
            release_date=premiered,
            seasons=tuple(seasons),
            backdrop_url=backdrop_url(row.get("backdrop_path")),
            original_title=_original(row.get("original_name"), row.get("name")),
            **enrichment,
        )

    def _season(self, tmdb_id: int, number: int, summary: dict[str, Any]) -> TmdbSeason:
        try:
            detail = self._get(f"/tv/{tmdb_id}/season/{number}")
        except TmdbError:
            detail = {}
        episodes: list[TmdbEpisode] = []
        for row in detail.get("episodes") or []:
            if not isinstance(row, dict) or not isinstance(row.get("episode_number"), int):
                continue
            episode_id = row.get("id")
            episodes.append(
                TmdbEpisode(
                    season=number,
                    episode=int(row["episode_number"]),
                    title=row.get("name") or None,
                    air_date=_day(row.get("air_date")),
                    tmdb_episode_id=int(episode_id) if isinstance(episode_id, int) else None,
                    still_url=still_url(row.get("still_path")),
                )
            )
        season_id = summary.get("id")
        count = summary.get("episode_count")
        return TmdbSeason(
            number=number,
            name=summary.get("name") or detail.get("name"),
            overview=summary.get("overview") or detail.get("overview"),
            air_date=_day(summary.get("air_date") or detail.get("air_date")),
            episode_count=int(count) if isinstance(count, int) else len(episodes) or None,
            tmdb_id=int(season_id) if isinstance(season_id, int) else None,
            episodes=tuple(episodes),
        )

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                raise TmdbError("TRANSPORT", "TMDB est injoignable.") from exc
            if response.status_code != 429:
                break
            if attempt == 2:
                raise TmdbError("RATE_LIMIT", "TMDB limite les requêtes.")
            self._sleeper(_retry_after(response.headers.get("Retry-After")))
        if response is None:
            raise TmdbError("TRANSPORT", "TMDB est injoignable.")
        if response.status_code == 401:
            raise TmdbError("UNAUTHORIZED", "Le jeton TMDB a été refusé.")
        if response.status_code == 404:
            raise TmdbError("NOT_FOUND", "Cette fiche TMDB est introuvable.")
        if response.status_code >= 400:
            raise TmdbError("BAD_RESPONSE", f"TMDB a répondu {response.status_code}.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise TmdbError("BAD_RESPONSE", "Réponse TMDB inattendue.")
        return payload
