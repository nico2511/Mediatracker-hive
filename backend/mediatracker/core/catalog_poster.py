"""Resolve catalog posters via TMDB (cached) with Wawacity and title-card fallbacks."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.catalog import choose_tmdb_hit, fold_title
from mediatracker.core.display_title import clean_display_title
from mediatracker.core.source_poster_cache import source_poster_api_url
from mediatracker.db.models import CatalogEntry, CatalogTmdbCache
from mediatracker.integrations.tmdb import TmdbClient, TmdbError, TmdbHit

logger = logging.getLogger(__name__)

_TMDB_TITLE_TAIL = re.compile(
    r"(?i)(?:\s*[-–—|]\s*(?:"
    r"french|truefrench|vostfr|vff|vf2|vfq|multi|vf|vo"
    r"))+\s*$"
)
_SEASON_TAIL = re.compile(r"(?i)\s*[-–—|]\s*saison\s+\d+\s*")
_BRACKETED = re.compile(r"\[[^\]]+\]")

_LIST_RESOLVE_CAP = 12
_REFRESH_RESOLVE_CAP = 25
_FOUND_TTL = timedelta(days=30)
_MISS_TTL = timedelta(days=7)


def tmdb_cache_key(row: CatalogEntry) -> str:
    year = "" if row.year is None else str(row.year)
    cleaned = fold_title(tmdb_search_title(row.title))[:240]
    return f"v2|{row.media_type}|{year}|{cleaned}"


def title_card_url(entry_id: int) -> str:
    return f"/api/v1/images/title-card/{entry_id}"


def tmdb_search_title(title: str) -> str:
    """Strip release noise before a TMDB lookup."""
    text = clean_display_title(title)
    text = _BRACKETED.sub(" ", text)
    text = _SEASON_TAIL.sub(" ", text)
    text = _TMDB_TITLE_TAIL.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" -–—:|") or title.strip()


def display_poster_url(row: CatalogEntry) -> str | None:
    if row.poster_url:
        return row.poster_url
    if row.source_poster_url:
        return source_poster_api_url(row.id)
    return title_card_url(row.id)


def _cache_valid(row: CatalogTmdbCache, now: datetime) -> bool:
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires > now


def _cache_row(db: Session, key: str) -> CatalogTmdbCache | None:
    """Stored row, or one added earlier in this session (autoflush is off)."""
    row = db.get(CatalogTmdbCache, key)
    if row is not None:
        return row
    for pending in db.new:
        if isinstance(pending, CatalogTmdbCache) and pending.cache_key == key:
            return pending
    return None


def _read_cache(db: Session, key: str, now: datetime) -> CatalogTmdbCache | None:
    row = _cache_row(db, key)
    if row is None or not _cache_valid(row, now):
        return None
    return row


def _write_cache(
    db: Session,
    key: str,
    now: datetime,
    *,
    found: bool,
    tmdb_id: int | None,
    poster_url: str | None,
) -> None:
    ttl = _FOUND_TTL if found else _MISS_TTL
    # Reuse a pending row: a second add with the same key fails at flush.
    row = _cache_row(db, key)
    if row is None:
        db.add(
            CatalogTmdbCache(
                cache_key=key,
                found=found,
                tmdb_id=tmdb_id,
                poster_url=poster_url,
                checked_at=now,
                expires_at=now + ttl,
            )
        )
        return
    row.found = found
    row.tmdb_id = tmdb_id
    row.poster_url = poster_url
    row.checked_at = now
    row.expires_at = now + ttl


def _apply_hit(row: CatalogEntry, chosen, genre_names: dict[int, str]) -> None:
    row.tmdb_id = chosen.tmdb_id
    row.poster_url = chosen.poster_url
    row.overview = chosen.overview
    row.rating = chosen.rating
    row.genres = [genre_names[genre_id] for genre_id in chosen.genre_ids if genre_id in genre_names]


def resolve_catalog_posters(
    db: Session,
    settings: Settings,
    rows: list[CatalogEntry],
    *,
    now: datetime | None = None,
    tmdb: TmdbClient | None = None,
    limit: int = _LIST_RESOLVE_CAP,
) -> tuple[int, int]:
    """Try TMDB for entries missing a TMDB poster. Returns (remote lookups, new matches)."""
    moment = now or datetime.now(timezone.utc)
    pending = [row for row in rows if not row.poster_url]
    if not pending or limit <= 0:
        return 0, 0
    client = tmdb
    if client is None:
        from mediatracker.core.tmdb.token import read_tmdb_token
        from mediatracker.integrations.tmdb import shared_tmdb

        token = read_tmdb_token(db, settings.secret_key)
        if not token:
            return 0, 0
        client = shared_tmdb(token)
    genres: dict[str, dict[int, str]] = {}
    # Keys looked up in this pass -> (hit or None, genre names). Same key, same answer.
    resolved: dict[str, tuple[TmdbHit | None, dict[int, str]]] = {}
    remote_calls = 0
    matched = 0
    for row in pending:
        if remote_calls >= limit:
            break
        key = tmdb_cache_key(row)
        if key in resolved:
            # No second TMDB call and no second cache row for this key.
            done, done_names = resolved[key]
            row.tmdb_checked_at = moment
            if done is not None:
                _apply_hit(row, done, done_names)
                matched += 1
            continue
        cached = _read_cache(db, key, moment)
        if cached is not None:
            row.tmdb_checked_at = cached.checked_at
            if cached.found and cached.poster_url:
                row.poster_url = cached.poster_url
                if cached.tmdb_id is not None:
                    row.tmdb_id = cached.tmdb_id
                matched += 1
            continue
        query = tmdb_search_title(row.title)
        try:
            hits = client.search(query, row.media_type)
        except TmdbError:
            logger.info("Résolution d'affiche catalogue interrompue (TMDB).")
            break
        remote_calls += 1
        row.tmdb_checked_at = moment
        chosen = choose_tmdb_hit(query, row.year, hits)
        if chosen is None:
            _write_cache(db, key, moment, found=False, tmdb_id=None, poster_url=None)
            resolved[key] = (None, {})
            continue
        names = genres.get(row.media_type)
        if names is None:
            try:
                names = client.genre_names(row.media_type)
            except TmdbError:
                names = {}
            genres[row.media_type] = names
        _apply_hit(row, chosen, names)
        resolved[key] = (chosen, names)
        _write_cache(
            db,
            key,
            moment,
            found=True,
            tmdb_id=chosen.tmdb_id,
            poster_url=chosen.poster_url,
        )
        matched += 1
    return remote_calls, matched


_TMDB_CACHE_RESET_KEY = "catalog_tmdb_poster_cache_reset_v2"


def migrate_tmdb_poster_cache_once(db: Session, *, now: datetime | None = None) -> int:
    """Once: drop TMDB poster misses and legacy v1 cache keys."""
    from sqlalchemy import delete, or_

    from mediatracker.db.models import Setting

    flag = db.get(Setting, _TMDB_CACHE_RESET_KEY)
    if flag is not None and flag.value == "true":
        return 0
    moment = now or datetime.now(timezone.utc)
    result = db.execute(
        delete(CatalogTmdbCache).where(
            or_(CatalogTmdbCache.found.is_(False), ~CatalogTmdbCache.cache_key.startswith("v2|"))
        )
    )
    removed = int(result.rowcount or 0)
    if flag is None:
        db.add(
            Setting(
                key=_TMDB_CACHE_RESET_KEY,
                value="true",
                is_secret=False,
                updated_at=moment,
            )
        )
    else:
        flag.value = "true"
        flag.updated_at = moment
    return removed


def resolve_pending_catalog_posters(
    db: Session,
    settings: Settings,
    *,
    now: datetime,
    tmdb: TmdbClient | None,
    limit: int = _REFRESH_RESOLVE_CAP,
) -> int:
    """Background-style pass after refresh: entries never checked first, then oldest."""
    from sqlalchemy import select

    from mediatracker.core.catalog import _match_order

    pending = list(db.scalars(select(CatalogEntry).where(CatalogEntry.poster_url.is_(None))).all())
    pending.sort(key=_match_order)
    _remote, matched = resolve_catalog_posters(db, settings, pending, now=now, tmdb=tmdb, limit=limit)
    return matched
