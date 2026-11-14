"""TMDB search for Jellyfin correction CSV export (exact candidate only)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mediatracker.core.tmdb.identify import identify_search
from mediatracker.integrations.tmdb import TmdbError

SearchHook = Callable[[str, str], tuple[int | None, str | None, int | None]]


class TmdbExportRateLimited(Exception):
    """Stop further TMDB searches for this export after a 429."""


@dataclass(frozen=True)
class TmdbCandidate:
    tmdb_id: int
    title: str
    year: int | None


def resolve_exact_tmdb_candidate(
    *,
    query: str,
    media_type: str,
    year: int | None = None,
    cache: dict[tuple[str, str, str], TmdbCandidate | None] | None = None,
    search_hook: SearchHook | None = None,
    client=None,
) -> TmdbCandidate | None:
    text = (query or "").strip()
    if not text:
        return None
    year_part = "" if year is None else str(year)
    key = (media_type, text.casefold(), year_part)
    if cache is not None and key in cache:
        return cache[key]
    if search_hook is not None:
        tmdb_id, title, hook_year = search_hook(text, media_type)
        result = TmdbCandidate(tmdb_id, title, hook_year) if tmdb_id and title else None
        if cache is not None:
            cache[key] = result
        return result
    if client is None:
        if cache is not None:
            cache[key] = None
        return None
    try:
        outcome = identify_search(
            client,
            text,
            year,
            "series" if media_type == "series" else "film",
            with_glance=False,
        )
    except TmdbError as exc:
        if cache is not None:
            cache[key] = None
        if exc.code == "RATE_LIMIT":
            raise TmdbExportRateLimited() from exc
        return None
    if outcome.hit is None:
        if cache is not None:
            cache[key] = None
        return None
    hit = outcome.hit
    result = TmdbCandidate(int(hit.tmdb_id), hit.title, hit.year)
    if cache is not None:
        cache[key] = result
    return result
