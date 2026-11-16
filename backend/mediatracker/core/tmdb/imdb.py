"""Resolve IMDb ids from TMDB for episode sources."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import Media

_IMDB_ID_DIGITS = re.compile(r"^\d{7,10}$")


def normalize_imdb_id(value: str) -> str:
    """Return a canonical ``tt…`` id. Accepts digits only, optional ``tt`` / ``TT``, and spaces."""
    collapsed = re.sub(r"\s+", "", (value or "").strip())
    if not collapsed:
        raise ValueError("Identifiant IMDb manquant.")
    lowered = collapsed.casefold()
    digits = collapsed[2:] if lowered.startswith("tt") else collapsed
    if not _IMDB_ID_DIGITS.fullmatch(digits):
        raise ValueError("Identifiant IMDb invalide (attendu : 7 à 10 chiffres, avec ou sans préfixe tt).")
    return f"tt{digits}"


def imdb_id_for_stremio(imdb_id: str | None) -> str | None:
    """Normalize an IMDb id for Stremio addon URLs, or ``None`` when missing / invalid."""
    raw = (imdb_id or "").strip()
    if not raw:
        return None
    try:
        return normalize_imdb_id(raw)
    except ValueError:
        return None


def ensure_imdb(db: Session, media: Media, settings: Settings) -> str | None:
    current = (media.imdb_id or "").strip()
    if current:
        try:
            normalized = normalize_imdb_id(current)
            if media.imdb_id != normalized:
                media.imdb_id = normalized
            return normalized
        except ValueError:
            pass
    if media.tmdb_id is None:
        return None
    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return None
    from mediatracker.core.tmdb.fetch import fetch_title

    fetched = fetch_title(db, settings, media.tmdb_id, media.type)
    if fetched is None:
        return None
    imdb = (fetched.imdb_id or "").strip()
    if imdb.startswith("tt"):
        media.imdb_id = imdb
        return imdb
    return None
