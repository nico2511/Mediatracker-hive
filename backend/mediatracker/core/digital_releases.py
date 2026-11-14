"""Sorties numériques: TMDB digital releases, cached so Home Assistant stays local.

``GET /ha/summary`` and ``GET /ha/releases`` only read this table. The worker
refreshes it from TMDB (release type 4, France) about twice a day.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.display_title import clean_display_title
from mediatracker.db.models import DigitalRelease, Media, Setting
from mediatracker.integrations.tmdb import DigitalReleaseHit, TmdbClient, TmdbError

logger = logging.getLogger(__name__)

_NEXT = "digital_releases.next_refresh_at"
_LIMIT = 12
_PAST_DAYS = 14
_AHEAD_DAYS = 28
_INTERVAL = timedelta(hours=12)
_RETRY = timedelta(hours=1)


def list_digital_releases(db: Session) -> list[dict]:
    """Rows already stored. ``tracked`` is true when that TMDB id is followed."""
    rows = list(
        db.scalars(
            select(DigitalRelease).order_by(DigitalRelease.release_on, DigitalRelease.title, DigitalRelease.id)
        ).all()
    )
    medias = list(db.scalars(select(Media).where(Media.tmdb_id.is_not(None))).all())
    followed = {(row.type, row.tmdb_id) for row in medias if row.followed and row.tmdb_id}
    media_ids = {(row.type, row.tmdb_id): row.id for row in medias if row.tmdb_id}
    found = []
    for row in rows:
        found.append(
            {
                "title": clean_display_title(row.title) or row.title,
                "poster_url": row.poster_url,
                "overview": (row.overview or "").strip() or None,
                "date": row.release_on,
                "type": "tv" if row.media_type == "series" else "movie",
                "tmdb_id": row.tmdb_id,
                "tracked": (row.media_type, row.tmdb_id) in followed,
                "media_id": media_ids.get((row.media_type, row.tmdb_id)),
            }
        )
    return found


def maybe_refresh_digital_releases(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client: TmdbClient | None = None,
) -> bool:
    """Refresh when the stored deadline has passed. A missing token waits out the interval."""
    moment = now or datetime.now(timezone.utc)
    nxt = _aware(_when(db, _NEXT))
    if nxt is not None and moment < nxt:
        return False
    try:
        changed = refresh_digital_releases(db, settings, now=moment, client=client)
    except TmdbError as exc:
        logger.info("Sorties numériques indisponibles (%s).", exc.code)
        _schedule(db, moment + _RETRY)
        db.commit()
        return False
    return changed


def refresh_digital_releases(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client: TmdbClient | None = None,
) -> bool:
    """Replace the cached window. Returns whether TMDB was called."""
    moment = now or datetime.now(timezone.utc)
    if client is None:
        from mediatracker.core.tmdb.token import read_tmdb_token

        token = read_tmdb_token(db, settings.secret_key)
        if not token:
            _schedule(db, moment + _INTERVAL)
            db.commit()
            return False
        from mediatracker.integrations.tmdb import shared_tmdb

        client = shared_tmdb(token)
    start = moment.date() - timedelta(days=_PAST_DAYS)
    end = moment.date() + timedelta(days=_AHEAD_DAYS)
    films = client.digital_releases(start=start, end=end)[:_LIMIT]
    series = client.airing_series(start=start, end=end)[:_LIMIT]
    db.execute(delete(DigitalRelease))
    _store_releases(db, films, media_type="film", moment=moment)
    _store_releases(db, series, media_type="series", moment=moment)
    _schedule(db, moment + _INTERVAL)
    db.commit()
    logger.info("Sorties numériques : %s film(s), %s série(s) à l'antenne.", len(films), len(series))
    return True


def _store_releases(db: Session, hits: list[DigitalReleaseHit], *, media_type: str, moment: datetime) -> None:
    seen: set[int] = set()
    for hit in hits:
        if hit.tmdb_id in seen:
            continue
        seen.add(hit.tmdb_id)
        overview = (hit.overview or "").strip() or None
        db.add(
            DigitalRelease(
                tmdb_id=hit.tmdb_id,
                media_type=media_type,
                title=hit.title[:300],
                poster_url=hit.poster_url,
                overview=overview[:4000] if overview else None,
                release_on=hit.release_date,
                refreshed_at=moment,
            )
        )



def _when(db: Session, key: str) -> datetime | None:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return parsed


def _schedule(db: Session, when: datetime) -> None:
    row = db.get(Setting, _NEXT)
    value = when.astimezone(timezone.utc).isoformat()
    if row is None:
        db.add(Setting(key=_NEXT, value=value, is_secret=False, updated_at=when))
        return
    row.value = value
    row.updated_at = when


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
