"""Métadonnées TMDB pour l'onglet Recherché (cache process, repli sans token)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import Media
from mediatracker.integrations.tmdb import TmdbClient, TmdbError

WANTED_TMDB_CACHE_TTL = timedelta(hours=6)
WANTED_TMDB_HTTP_TIMEOUT_S = 4.0

_WANTED_TMDB_CACHE: dict[tuple[str, int], tuple[datetime, dict[str, Any]]] = {}


@dataclass(frozen=True)
class WantedTmdbMeta:
    title: str
    year: int | None
    poster_url: str | None
    overview: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "year": self.year,
            "poster_url": self.poster_url,
            "overview": self.overview,
        }


def clear_wanted_tmdb_cache() -> None:
    _WANTED_TMDB_CACHE.clear()


def _fallback_meta(*, tmdb_id: int) -> WantedTmdbMeta:
    return WantedTmdbMeta(
        title=f"Fiche TMDB {tmdb_id}",
        year=None,
        poster_url=None,
        overview=None,
    )


def _meta_from_media(media: Media) -> WantedTmdbMeta:
    title = (media.title or "").strip() or f"Fiche TMDB {media.tmdb_id}"
    return WantedTmdbMeta(
        title=title,
        year=media.year,
        poster_url=media.poster_url,
        overview=(media.overview or None),
    )


def _cache_get(media_type: str, tmdb_id: int, now: datetime) -> dict[str, Any] | None:
    row = _WANTED_TMDB_CACHE.get((media_type, tmdb_id))
    if row is None:
        return None
    cached_at, payload = row
    if now - cached_at > WANTED_TMDB_CACHE_TTL:
        _WANTED_TMDB_CACHE.pop((media_type, tmdb_id), None)
        return None
    return dict(payload)


def _cache_put(media_type: str, tmdb_id: int, now: datetime, meta: WantedTmdbMeta) -> dict[str, Any]:
    payload = meta.as_dict()
    _WANTED_TMDB_CACHE[(media_type, tmdb_id)] = (now, payload)
    return payload


def fetch_wanted_tmdb_meta(
    db: Session,
    settings: Settings,
    *,
    media_type: str,
    tmdb_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Résout titre / année / affiche avec cache ; repli si pas de jeton ou TMDB indisponible."""
    moment = now or datetime.now(timezone.utc)
    hit = _cache_get(media_type, tmdb_id, moment)
    if hit is not None:
        return hit

    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is not None and (media.title or "").strip():
        return _cache_put(media_type, tmdb_id, moment, _meta_from_media(media))

    token = tmdb_token.read_tmdb_token(db, settings.secret_key)
    if not token:
        return _cache_put(media_type, tmdb_id, moment, _fallback_meta(tmdb_id=tmdb_id))

    client = TmdbClient(token, request_timeout=WANTED_TMDB_HTTP_TIMEOUT_S)
    try:
        fetched = client.fetch(tmdb_id, media_type)
        meta = WantedTmdbMeta(
            title=fetched.title,
            year=fetched.year,
            poster_url=fetched.poster_url,
            overview=fetched.overview,
        )
        return _cache_put(media_type, tmdb_id, moment, meta)
    except TmdbError:
        return _cache_put(media_type, tmdb_id, moment, _fallback_meta(tmdb_id=tmdb_id))
    finally:
        client.close()