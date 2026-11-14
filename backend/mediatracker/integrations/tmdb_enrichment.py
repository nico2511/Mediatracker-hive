"""Persist TMDB detail fields on media (no dependency on core.tmdb)."""

from __future__ import annotations

from datetime import date
from typing import Any

from mediatracker.db.models import Media
from mediatracker.integrations.tmdb import TmdbTitle

TMDB_DIGITAL = 4
TMDB_PHYSICAL = 5
TMDB_THEATRICAL = 3
_REGIONS = ("FR", "US")


def _parse_day(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _release_entries(release_dates: dict[str, Any] | None) -> list[tuple[str, int, date]]:
    if not isinstance(release_dates, dict):
        return []
    rows: list[tuple[str, int, date]] = []
    for block in release_dates.get("results") or []:
        if not isinstance(block, dict):
            continue
        country = str(block.get("iso_3166_1") or "").upper()
        for item in block.get("release_dates") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if not isinstance(kind, int):
                continue
            day = _parse_day(item.get("release_date"))
            if day is not None:
                rows.append((country, kind, day))
    return rows


def _first_in_region(
    entries: list[tuple[str, int, date]],
    *,
    region: str,
    kinds: tuple[int, ...],
) -> date | None:
    pool = [day for iso, kind, day in entries if iso == region and kind in kinds]
    return min(pool) if pool else None


def release_by_region(release_dates: dict[str, Any] | None) -> dict[str, dict[str, str | None]]:
    entries = _release_entries(release_dates)
    out: dict[str, dict[str, str | None]] = {}
    for region in _REGIONS:
        theatrical = _first_in_region(entries, region=region, kinds=(TMDB_THEATRICAL,))
        digital = _first_in_region(entries, region=region, kinds=(TMDB_DIGITAL,))
        physical = _first_in_region(entries, region=region, kinds=(TMDB_PHYSICAL,))
        out[region] = {
            "theatrical": theatrical.isoformat() if theatrical else None,
            "digital": digital.isoformat() if digital else None,
            "physical": physical.isoformat() if physical else None,
        }
    return out


def providers_fr_from_row(row: dict[str, Any]) -> list[str]:
    block = row.get("watch/providers")
    if not isinstance(block, dict):
        return []
    fr = (block.get("results") or {}).get("FR")
    if not isinstance(fr, dict):
        return []
    names: list[str] = []
    for key in ("flatrate", "rent", "buy"):
        for item in fr.get(key) or []:
            if isinstance(item, dict) and item.get("provider_name"):
                name = str(item["provider_name"])
                if name not in names:
                    names.append(name)
    return names


def detail_enrichment_from_row(
    row: dict[str, Any],
    *,
    media_type: str,
    release_dates: dict[str, Any] | None,
) -> dict[str, Any]:
    genres = tuple(
        str(item["name"])
        for item in row.get("genres") or []
        if isinstance(item, dict) and item.get("name")
    )
    collection = row.get("belongs_to_collection")
    collection_id = None
    collection_name = None
    if isinstance(collection, dict):
        if isinstance(collection.get("id"), int):
            collection_id = int(collection["id"])
        if collection.get("name"):
            collection_name = str(collection["name"])
    runtime = row.get("runtime") if media_type == "film" else None
    if media_type == "series":
        runs = row.get("episode_run_time") or []
        runtime = runs[0] if isinstance(runs, list) and runs else None
    vote_average = row.get("vote_average")
    vote_count = row.get("vote_count")
    language = row.get("original_language")
    providers = tuple(providers_fr_from_row(row))
    by_region = release_by_region(release_dates) if release_dates else None
    external = row.get("external_ids")
    imdb_id = None
    if isinstance(external, dict):
        imdb = external.get("imdb_id")
        if isinstance(imdb, str) and imdb.startswith("tt"):
            imdb_id = imdb
    return {
        "vote_average": float(vote_average) if isinstance(vote_average, (int, float)) else None,
        "vote_count": int(vote_count) if isinstance(vote_count, int) else None,
        "genres": genres,
        "runtime_minutes": int(runtime) if isinstance(runtime, int) else None,
        "original_language": str(language) if isinstance(language, str) and language else None,
        "collection_id": collection_id,
        "collection_name": collection_name,
        "watch_providers_fr": providers,
        "release_by_region": by_region,
        "imdb_id": imdb_id,
    }


def apply_tmdb_enrichment(media: Media, fetched: TmdbTitle) -> None:
    media.tmdb_vote_average = fetched.vote_average
    media.tmdb_vote_count = fetched.vote_count
    media.tmdb_genres = list(fetched.genres) if fetched.genres else None
    media.tmdb_runtime_min = fetched.runtime_minutes
    media.tmdb_original_language = fetched.original_language
    media.tmdb_collection_id = fetched.collection_id
    media.tmdb_collection_name = fetched.collection_name
    media.tmdb_watch_providers_fr = list(fetched.watch_providers_fr) if fetched.watch_providers_fr else None
    if fetched.release_by_region:
        media.tmdb_release_by_region = fetched.release_by_region
    if fetched.imdb_id and not media.imdb_id:
        media.imdb_id = fetched.imdb_id
