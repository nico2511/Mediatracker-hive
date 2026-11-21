"""When a followed film may be searched: TMDB digital/physical dates and fallbacks."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.tmdb import TmdbTitle
from mediatracker.integrations.tmdb_enrichment import TMDB_DIGITAL, TMDB_PHYSICAL, TMDB_THEATRICAL

_SETTING_FALLBACK_MONTHS = "film_theatrical_fallback_months"
_SETTING_TRIGGER_US_DIGITAL = "film_trigger_us_digital"
_TRUSTED_DIGITAL_SOURCES = frozenset({"digital_fr", "physical_fr", "digital_any", "digital_us"})


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last = monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _parse_day(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _entries(release_dates: dict[str, Any] | None) -> list[tuple[str, int, date]]:
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


def _first_date(
    entries: list[tuple[str, int, date]],
    *,
    country: str | None,
    kinds: tuple[int, ...],
) -> date | None:
    pool = [day for iso, kind, day in entries if kind in kinds and (country is None or iso == country)]
    return min(pool) if pool else None


def _digital_source(country: str) -> str:
    if country == "FR":
        return "digital_fr"
    return f"digital_{country.lower()}"


def _physical_source(country: str) -> str:
    if country == "FR":
        return "physical_fr"
    return f"physical_{country.lower()}"


def _trusted_stored_source(source: str | None) -> bool:
    if source is None:
        return True
    if source in _TRUSTED_DIGITAL_SOURCES:
        return True
    return source.startswith("digital_") or source.startswith("physical_")


def compute_film_availability(
    release_dates: dict[str, Any] | None,
    theatrical: date | None,
    *,
    fallback_months: int = 4,
    country: str = "FR",
    trigger_us_digital: bool = False,
) -> tuple[date | None, date | None, str]:
    """Return (search_from, theatrical_on, source_code).

    Priority: regional digital (optionally earliest with US digital), regional physical,
    any digital/physical, theatrical + N months.
    """
    catalog = country.upper()
    entries = _entries(release_dates)
    theatrical_on = _first_date(entries, country=catalog, kinds=(TMDB_THEATRICAL,)) or theatrical
    digital_region = _first_date(entries, country=catalog, kinds=(TMDB_DIGITAL,))
    digital_us = _first_date(entries, country="US", kinds=(TMDB_DIGITAL,)) if trigger_us_digital else None

    if digital_region is not None or digital_us is not None:
        picks: list[tuple[date, str]] = []
        if digital_region is not None:
            picks.append((digital_region, _digital_source(catalog)))
        if digital_us is not None:
            picks.append((digital_us, "digital_us"))
        if picks:
            available, source = min(picks, key=lambda row: row[0])
            return available, theatrical_on, source

    physical_region = _first_date(entries, country=catalog, kinds=(TMDB_PHYSICAL,))
    if physical_region is not None:
        return physical_region, theatrical_on, _physical_source(catalog)
    any_digital = _first_date(entries, country=None, kinds=(TMDB_DIGITAL, TMDB_PHYSICAL))
    if any_digital is not None:
        return any_digital, theatrical_on, "digital_any"
    if theatrical_on is not None and fallback_months >= 0:
        return _add_months(theatrical_on, fallback_months), theatrical_on, "theatrical_fallback"
    if theatrical_on is not None:
        return theatrical_on, theatrical_on, "theatrical_only"
    return None, theatrical_on, "none"


def fallback_months_from_db(db: Session, default: int = 4) -> int:
    from mediatracker.db.models import Setting

    row = db.get(Setting, _SETTING_FALLBACK_MONTHS)
    if row is None or not row.value:
        return default
    try:
        value = int(row.value)
    except ValueError:
        return default
    return max(0, min(value, 24))


def trigger_us_digital_from_db(db: Session, default: bool = False) -> bool:
    from mediatracker.db.models import Setting

    row = db.get(Setting, _SETTING_TRIGGER_US_DIGITAL)
    if row is None or row.value is None or not str(row.value).strip():
        return default
    return str(row.value).strip().lower() in {"1", "true", "yes", "on"}


def catalog_region_from_db(db: Session, default: str = "FR") -> str:
    from mediatracker.core.tmdb.catalog_settings import read_tmdb_catalog_settings

    return read_tmdb_catalog_settings(db).region or default


def _sync_theatrical(db: Session, media: Media, theatrical: date | None) -> None:
    if theatrical is None:
        return
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    film = next((item for item in items if item.season is None and item.episode is None), None)
    if film is not None:
        film.air_date = theatrical


def _sync_release_by_region(media: Media, release_dates: dict[str, Any] | None, catalog_region: str) -> None:
    if not release_dates:
        return
    from mediatracker.integrations.tmdb_enrichment import regions_for_display, release_by_region

    media.tmdb_release_by_region = release_by_region(
        release_dates,
        regions=regions_for_display(catalog_region),
    )


def apply_film_from_tmdb(db: Session, media: Media, fetched: TmdbTitle, *, fallback_months: int | None = None) -> None:
    if media.type != "film" or fetched.media_type != "film":
        return
    months = fallback_months if fallback_months is not None else fallback_months_from_db(db)
    catalog_region = catalog_region_from_db(db)
    trigger_us = trigger_us_digital_from_db(db)
    theatrical = fetched.release_date
    _sync_release_by_region(media, fetched.release_dates, catalog_region)
    if fetched.release_dates is None:
        _sync_theatrical(db, media, theatrical)
        if media.next_air_date is not None and _trusted_stored_source(media.film_availability_source):
            return
        available, _theatrical, source = compute_film_availability(
            {"results": []},
            theatrical,
            fallback_months=months,
            country=catalog_region,
            trigger_us_digital=trigger_us,
        )
        media.next_air_date = available
        media.film_availability_source = source
        return
    available, theatrical_on, source = compute_film_availability(
        fetched.release_dates,
        theatrical,
        fallback_months=months,
        country=catalog_region,
        trigger_us_digital=trigger_us,
    )
    media.next_air_date = available
    media.film_availability_source = source
    _sync_theatrical(db, media, theatrical_on)


def _format_fr(day: date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


def film_availability_label(
    media: Media,
    episodes: list[MediaItem] | None = None,
    *,
    today: date | None = None,
) -> str | None:
    """French status line for a film fiche when the file is not on disk yet."""
    if media.type != "film":
        return None
    day = today or date.today()
    when = media.next_air_date
    source = media.film_availability_source or ""
    theatrical = None
    if episodes:
        film = next((item for item in episodes if item.season is None and item.episode is None), None)
        theatrical = film.air_date if film is not None else None
    if when is not None and when > day:
        if source == "physical_fr" or source.startswith("physical_"):
            return f"Pas encore disponible, sortie physique le {_format_fr(when)}"
        if source == "theatrical_fallback":
            return f"Pas encore disponible, disponible vers le {_format_fr(when)} (estimation)"
        return f"Pas encore disponible, sortie numérique le {_format_fr(when)}"
    if theatrical is not None and theatrical > day:
        return f"Pas encore sorti, sortie en salle le {_format_fr(theatrical)}"
    if when is None and theatrical is None:
        return "Pas encore sorti"
    return None


def _film_item_on_disk(episodes: list[MediaItem] | None) -> bool:
    if not episodes:
        return False
    film = next((item for item in episodes if item.season is None and item.episode is None), None)
    if film is None:
        return False
    return bool(film.downloaded or film.file_path)


def film_not_yet_available(
    media: Media,
    episodes: list[MediaItem] | None = None,
    *,
    today: date | None = None,
    file_present: bool = False,
) -> bool:
    if media.type != "film":
        return False
    if file_present or _film_item_on_disk(episodes):
        return False
    if episodes:
        film = next((item for item in episodes if item.season is None and item.episode is None), None)
        if film is not None and film.watched:
            return False
    day = today or date.today()
    when = media.next_air_date
    if when is None:
        return False
    return when > day