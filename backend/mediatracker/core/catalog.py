"""Latest-releases catalog. Sources feed one list; the same title is stored once.

The HTTP pause, the error backoff and the page cache are the ones the source poll
already uses. This module does not import a site.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.public_url import effective_public_base_url
from mediatracker.core.source_poll import close_source
from mediatracker.db.models import CatalogEntry, Setting
from mediatracker.events import record_event
from mediatracker.events.deliver import page_url
from mediatracker.integrations.tmdb import TmdbClient, TmdbHit
from mediatracker.providers.base import CapabilityNotSupported, ReleaseListing, SourceFetchError
from mediatracker.providers.sources.throttle import backoff_delay

logger = logging.getLogger(__name__)

_LAST = "catalog.last_refresh_at"
_NEXT = "catalog.next_refresh_at"
_STREAK = "catalog.error_streak"
_ERROR = "catalog.last_error"
_MATCH_CAP = 25
_DIGEST_CAP = 30


@dataclass(frozen=True)
class RefreshReport:
    added: int
    updated: int
    matched: int
    purged: int
    skipped: bool
    error: str | None
    refreshed_at: datetime | None
    next_refresh_at: datetime | None


def fold_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("œ", "oe").replace("æ", "ae")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def dedupe_key(
    title: str,
    year: int | None,
    media_type: str,
    season: int | None,
    episode: int | None,
) -> str:
    year_part = "" if year is None else str(year)
    season_part = "" if season is None else str(season)
    episode_part = "" if episode is None else str(episode)
    return f"{fold_title(title)[:240]}|{year_part}|{media_type}|{season_part}|{episode_part}"


def _latest(latest, media_type: str, quality: str | None, language: str | None):
    """Pass the catalog filters when the source accepts them. Older sources do not."""
    try:
        import inspect

        parameters = inspect.signature(latest).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs = {}
    if "quality" in parameters:
        kwargs["quality"] = quality or "HD"
    if "language" in parameters:
        kwargs["language"] = language or "VFF"
    return latest(media_type, **kwargs)


def listing_kept(item: ReleaseListing, quality: str | None, language: str | None) -> bool:
    if not (item.title or "").strip() or not item.url:
        return False
    if quality and item.quality != quality:
        return False
    if language and item.language != language:
        return False
    return True


def choose_tmdb_hit(title: str, year: int | None, hits: list[TmdbHit]) -> TmdbHit | None:
    from mediatracker.core.tmdb.identify import identify
    from mediatracker.library.parse import ParsedName

    parsed = ParsedName(
        title=title,
        year=year,
        season=None,
        episode=None,
        tmdb_id=None,
        quality=None,
        language=None,
        media_type=hits[0].media_type if hits else "film",
    )
    return identify(hits, parsed).hit


def refresh_due(db: Session, now: datetime) -> bool:
    nxt = _parse_dt(_text(db, _NEXT))
    return nxt is None or now >= nxt


def maybe_refresh_catalog(db: Session, settings: Settings, **kwargs) -> RefreshReport | None:
    """One catalog pass when the interval (or the error backoff) has elapsed."""
    moment = kwargs.get("now") or datetime.now(timezone.utc)
    if kwargs.get("sources") is None and not refresh_due(db, moment):
        return None
    return refresh_catalog(
        db,
        settings,
        now=moment,
        sources=kwargs.get("sources"),
        tmdb=kwargs.get("tmdb"),
        force=False,
    )


def refresh_catalog(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    sources: list | None = None,
    tmdb: TmdbClient | None = None,
    force: bool = False,
) -> RefreshReport:
    moment = now or datetime.now(timezone.utc)
    if not force and not refresh_due(db, moment):
        last, nxt, error = catalog_clock(db)
        return RefreshReport(0, 0, 0, 0, True, error, last, nxt)
    owned_sources = sources is None
    if sources is None:
        from mediatracker.providers.sources.loading import sources_for_catalog

        sources = sources_for_catalog(db, settings)
    try:
        return _refresh(db, settings, moment, sources, tmdb)
    finally:
        if owned_sources:
            for _provider_id, _source_type, source in sources:
                close_source(source)


def catalog_clock(db: Session) -> tuple[datetime | None, datetime | None, str | None]:
    error = _text(db, _ERROR)
    return _parse_dt(_text(db, _LAST)), _parse_dt(_text(db, _NEXT)), error or None


def _refresh(
    db: Session,
    settings: Settings,
    moment: datetime,
    sources: list,
    tmdb: TmdbClient | None,
) -> RefreshReport:
    quality = _filter_value(db, "catalog_quality", settings.catalog_quality)
    language = _filter_value(db, "catalog_language", settings.catalog_language)
    interval = timedelta(hours=_int_setting(db, "catalog_interval_hours", settings.catalog_interval_hours))
    retention_days = _int_setting(db, "catalog_retention_days", settings.catalog_retention_days)
    backoff_min = _int_setting(db, "source_error_backoff_min", settings.source_error_backoff_min)

    if not sources:
        _schedule(db, moment, interval, streak=0, error="Aucune source de nouveautés n'est configurée.", failed=False)
        db.commit()
        last, nxt, error = catalog_clock(db)
        return RefreshReport(0, 0, 0, 0, False, error, last, nxt)

    added = 0
    updated = 0
    pages = 0
    errors: list[str] = []
    created_ids: list[int] = []
    for _provider_id, source_type, source in sources:
        if "latest" not in getattr(source, "capabilities", frozenset()):
            continue
        latest = getattr(source, "latest_releases", None)
        if latest is None:
            continue
        for media_type in ("film", "series"):
            try:
                listings = _latest(latest, media_type, quality, language)
            except CapabilityNotSupported:
                continue
            except SourceFetchError as exc:
                errors.append(exc.message)
                continue
            pages += 1
            for item in listings:
                if not isinstance(item, ReleaseListing) or not listing_kept(item, quality, language):
                    continue
                row, created = _upsert(db, item, str(source_type), moment)
                if created:
                    added += 1
                    created_ids.append(row.id)
                else:
                    updated += 1

    from mediatracker.core.catalog_poster import resolve_pending_catalog_posters

    matched = 0
    try:
        with db.begin_nested():
            matched = resolve_pending_catalog_posters(db, settings, now=moment, tmdb=tmdb, limit=_MATCH_CAP)
    except Exception:
        logger.exception("Résolution des affiches TMDB échouée ; le rafraîchissement du catalogue continue.")

    try:
        _merge_same_tmdb(db)
        purged = _purge(db, moment, retention_days)
        fresh = [row for row in (_get(db, item_id) for item_id in created_ids) if row is not None and not row.ignored]
        if fresh:
            _digest(db, settings, fresh, moment)
        failed = pages == 0 and bool(errors)
        streak = _streak(db) + 1 if failed else 0
        message = " ".join(dict.fromkeys(errors))[:500] if errors else ""
        delay = max(interval, backoff_delay(streak, backoff_min)) if failed else interval
        _schedule(db, moment, delay, streak=streak, error=message, failed=failed)
        if failed and streak == 1:
            _record_catalog_failed(db, moment, message or "Rafraîchissement du catalogue impossible.")
        db.commit()
        logger.info(
            "Catalogue : %s ajout(s), %s mise(s) à jour, %s correspondance(s) TMDB, %s purge(s).",
            added,
            updated,
            matched,
            purged,
        )
        last, nxt, error = catalog_clock(db)
        return RefreshReport(added, updated, matched, purged, False, error, last, nxt)
    except Exception as exc:
        db.rollback()
        streak = _streak(db) + 1
        short = (str(exc) or type(exc).__name__)[:500]
        delay = max(interval, backoff_delay(streak, backoff_min))
        _schedule(db, moment, delay, streak=streak, error=short, failed=True)
        if streak == 1:
            _record_catalog_failed(db, moment, short)
        db.commit()
        logger.exception("Rafraîchissement du catalogue interrompu : %s", short)
        last, nxt, error = catalog_clock(db)
        return RefreshReport(added, updated, matched, 0, False, error, last, nxt)


def _upsert(db: Session, item: ReleaseListing, source_type: str, now: datetime) -> tuple[CatalogEntry, bool]:
    key = dedupe_key(item.title, item.year, item.media_type, item.season, item.episode)
    row = db.scalar(select(CatalogEntry).where(CatalogEntry.dedupe_key == key))
    created = False
    if row is None:
        row = CatalogEntry(
            dedupe_key=key,
            title=item.title[:300],
            title_folded=fold_title(item.title)[:300],
            year=item.year,
            media_type=item.media_type,
            season=item.season,
            episode=item.episode,
            quality=item.quality,
            language=item.language,
            source=source_type[:64],
            source_url=item.url,
            sources=[],
            first_seen_at=now,
            last_seen_at=now,
            genres=[],
            ignored=False,
            source_poster_url=getattr(item, "source_poster_url", None),
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            created = True
        except IntegrityError:
            row = db.scalar(select(CatalogEntry).where(CatalogEntry.dedupe_key == key))
            if row is None:
                raise
    if not created:
        row.last_seen_at = now
        if not row.quality and item.quality:
            row.quality = item.quality
        if not row.language and item.language:
            row.language = item.language
    poster = getattr(item, "source_poster_url", None)
    if poster and row.source_poster_url != poster:
        row.source_poster_url = poster
    _remember_source(row, source_type, item)
    return row, created


def _remember_source(row: CatalogEntry, source_type: str, item: ReleaseListing) -> None:
    current = [dict(entry) for entry in (row.sources or []) if isinstance(entry, dict)]
    for entry in current:
        if entry.get("source") == source_type and entry.get("url") == item.url:
            return
    current.append(
        {
            "source": source_type,
            "url": item.url,
            "quality": item.quality,
            "language": item.language,
        }
    )
    row.sources = current


def _merge_same_tmdb(db: Session) -> None:
    rows = list(db.scalars(select(CatalogEntry).where(CatalogEntry.tmdb_id.is_not(None))).all())
    groups: dict[tuple, list[CatalogEntry]] = {}
    for row in rows:
        groups.setdefault((row.tmdb_id, row.media_type, row.season, row.episode), []).append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=_seen_order)
        keep = group[0]
        for other in group[1:]:
            _absorb(keep, other)
            db.delete(other)


def _absorb(keep: CatalogEntry, other: CatalogEntry) -> None:
    seen = [dict(item) for item in (keep.sources or []) if isinstance(item, dict)]
    known = {(item.get("source"), item.get("url")) for item in seen}
    for item in other.sources or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("source"), item.get("url"))
        if key in known:
            continue
        seen.append(dict(item))
        known.add(key)
    keep.sources = seen
    first_keep = _as_utc(keep.first_seen_at)
    first_other = _as_utc(other.first_seen_at)
    if first_other is not None and (first_keep is None or first_other < first_keep):
        keep.first_seen_at = other.first_seen_at
    last_keep = _as_utc(keep.last_seen_at)
    last_other = _as_utc(other.last_seen_at)
    if last_other is not None and (last_keep is None or last_other > last_keep):
        keep.last_seen_at = other.last_seen_at
    if other.ignored:
        keep.ignored = True
    if not keep.poster_url and other.poster_url:
        keep.poster_url = other.poster_url
    if not keep.overview and other.overview:
        keep.overview = other.overview
    if keep.rating is None and other.rating is not None:
        keep.rating = other.rating
    if not keep.genres and other.genres:
        keep.genres = list(other.genres)
    if not keep.quality and other.quality:
        keep.quality = other.quality
    if not keep.language and other.language:
        keep.language = other.language
    if not keep.source_poster_url and other.source_poster_url:
        keep.source_poster_url = other.source_poster_url
    if keep.tmdb_id is None and other.tmdb_id is not None:
        keep.tmdb_id = other.tmdb_id


def _purge(db: Session, now: datetime, days: int) -> int:
    cutoff = now - timedelta(days=days)
    removed = 0
    for row in list(db.scalars(select(CatalogEntry)).all()):
        seen = _as_utc(row.last_seen_at)
        if seen is not None and seen < cutoff:
            db.delete(row)
            removed += 1
    return removed


def _digest(db: Session, settings: Settings, fresh: list[CatalogEntry], now: datetime) -> None:
    items = [
        {
            "title": row.title,
            "year": row.year,
            "media_type": row.media_type,
            "season": row.season,
            "episode": row.episode,
            "tmdb_id": row.tmdb_id,
            "poster_url": row.poster_url,
        }
        for row in fresh[:_DIGEST_CAP]
    ]
    poster = next((row.poster_url for row in fresh if row.poster_url), None)
    record_event(
        db,
        "nouveautes",
        level="info",
        payload={
            "grouped": True,
            "count": len(fresh),
            "items": items,
            "level": "info",
            "poster_url": poster,
            "page_url": page_url(effective_public_base_url(db, settings) or "", None),
        },
        dedupe_key=f"nouveautes:{now.isoformat()}",
        when=now,
    )


def _match_order(row: CatalogEntry) -> tuple[datetime, int]:
    checked = _as_utc(row.tmdb_checked_at) or datetime.min.replace(tzinfo=timezone.utc)
    return checked, row.id


def _seen_order(row: CatalogEntry) -> tuple[datetime, int]:
    seen = _as_utc(row.first_seen_at) or datetime.max.replace(tzinfo=timezone.utc)
    return seen, row.id


def _record_catalog_failed(db: Session, moment: datetime, message: str) -> None:
    record_event(
        db,
        "catalog.failed",
        level="error",
        payload={"message": message[:500], "level": "error"},
        when=moment,
    )


def _schedule(db: Session, now: datetime, delay: timedelta, *, streak: int, error: str, failed: bool) -> None:
    del failed
    _put(db, _LAST, now.isoformat(), now)
    _put(db, _NEXT, (now + delay).isoformat(), now)
    _put(db, _STREAK, str(streak), now)
    _put(db, _ERROR, error, now)


def _get(db: Session, entry_id: int) -> CatalogEntry | None:
    return db.get(CatalogEntry, entry_id)


def _filter_value(db: Session, key: str, default: str) -> str | None:
    row = db.get(Setting, key)
    if row is None:
        text = (default or "").strip()
        return text or None
    text = (row.value or "").strip()
    return text or None



def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _int_setting(db: Session, key: str, default: int) -> int:
    raw = _text(db, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _streak(db: Session) -> int:
    return _int_setting(db, _STREAK, 0)


def _put(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.is_secret = False
    row.updated_at = now


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
