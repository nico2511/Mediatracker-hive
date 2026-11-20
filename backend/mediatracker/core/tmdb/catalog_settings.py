"""TMDB catalogue display settings (language, region, original-language filter)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from mediatracker.integrations.tmdb import TmdbHit

_DISPLAY_LANG = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
_REGION = re.compile(r"^[A-Z]{2}$")
_ISO_639_1 = re.compile(r"^[a-z]{2}$")

DEFAULT_DISPLAY_LANGUAGE = "fr-FR"
DEFAULT_REGION = "FR"

_TMDB_ORIGINAL_LANGS_KEY = "tmdb_original_languages"


@dataclass(frozen=True)
class TmdbCatalogSettings:
    display_language: str
    region: str
    original_languages: tuple[str, ...]


def _row_text(db: Session, key: str) -> str:
    from mediatracker.db.models import Setting

    row = db.get(Setting, key)
    return (row.value or "").strip() if row is not None else ""


def read_tmdb_catalog_settings(db: Session) -> TmdbCatalogSettings:
    lang = _row_text(db, "tmdb_display_language") or DEFAULT_DISPLAY_LANGUAGE
    region = _row_text(db, "tmdb_region") or DEFAULT_REGION
    raw_langs = _row_text(db, _TMDB_ORIGINAL_LANGS_KEY)
    langs = tuple(part.strip().lower() for part in raw_langs.split(",") if part.strip()) if raw_langs else ()
    return TmdbCatalogSettings(
        display_language=lang if _DISPLAY_LANG.match(lang) else DEFAULT_DISPLAY_LANGUAGE,
        region=region if _REGION.match(region) else DEFAULT_REGION,
        original_languages=langs,
    )


def with_original_language_param(languages: tuple[str, ...]) -> str | None:
    if not languages:
        return None
    return "|".join(languages)


def filter_hits_by_original_language(hits: list[TmdbHit], languages: tuple[str, ...]) -> list[TmdbHit]:
    if not languages:
        return hits
    allowed = {code.casefold() for code in languages}
    found: list[TmdbHit] = []
    for hit in hits:
        code = hit.original_language
        if code and code.casefold() in allowed:
            found.append(hit)
    return found


def validate_display_language(value: str) -> str:
    text = value.strip()
    if not _DISPLAY_LANG.match(text):
        raise ValueError("Le format attendu est xx-YY (ex. fr-FR).")
    return text


def validate_region(value: str) -> str:
    text = value.strip().upper()
    if not _REGION.match(text):
        raise ValueError("Code pays ISO 3166-1 alpha-2 attendu (ex. FR).")
    return text


def validate_original_languages(value: str) -> str:
    if not value.strip():
        return ""
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    for part in parts:
        if not _ISO_639_1.match(part):
            raise ValueError(f"Langue « {part} » : code ISO 639-1 attendu (deux lettres).")
    return ",".join(dict.fromkeys(parts))


def catalog_settings_cache_fragment(settings: TmdbCatalogSettings) -> dict[str, str | tuple[str, ...]]:
    return {
        "display_language": settings.display_language,
        "region": settings.region,
        "original_languages": settings.original_languages,
    }


__all__ = [
    "DEFAULT_DISPLAY_LANGUAGE",
    "DEFAULT_REGION",
    "TmdbCatalogSettings",
    "catalog_settings_cache_fragment",
    "filter_hits_by_original_language",
    "read_tmdb_catalog_settings",
    "validate_display_language",
    "validate_original_languages",
    "validate_region",
    "with_original_language_param",
]
