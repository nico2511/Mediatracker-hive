"""Notice when TMDB adds a season that was not on the fiche yet."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, MediaItem, MediaSeason, Setting
from mediatracker.events import record_event
from mediatracker.integrations.tmdb import TmdbError, TmdbTitle

REFRESH_EVERY = timedelta(hours=24)
REFRESH_UNFOLLOWED_EVERY = timedelta(days=7)
_IDENTIFY_ATTEMPT_EVERY = timedelta(days=1)
_IDENTIFY_BACKOFF = timedelta(minutes=15)
_CURSOR = "tmdb.season_cursor"
_FILM_CURSOR = "tmdb.film_cursor"
_UNFOLLOWED_CURSOR = "tmdb.unfollowed_enrich_cursor"
_IDENTIFY_CURSOR = "tmdb.identify_retry_cursor"
_IDENTIFY_BACKOFF_KEY = "tmdb.identify_backoff_until"


def apply_new_seasons(db: Session, media: Media, fetched: TmdbTitle, now: datetime) -> list[int]:
    """Insert seasons that are not stored yet and emit ``season.announced`` once each."""
    if media.type != "series":
        if media.type == "film":
            from mediatracker.core.film_availability import apply_film_from_tmdb
            from mediatracker.integrations.tmdb_enrichment import apply_tmdb_enrichment

            apply_film_from_tmdb(db, media, fetched)
            apply_tmdb_enrichment(media, fetched)
        media.tmdb_refreshed_at = now
        return []
    known = {
        row.number
        for row in db.scalars(select(MediaSeason).where(MediaSeason.media_id == media.id)).all()
    }
    announced: list[int] = []
    for season in fetched.seasons:
        if season.number in known:
            continue
        db.add(
            MediaSeason(
                media_id=media.id,
                number=season.number,
                name=season.name,
                overview=season.overview,
                air_date=season.air_date,
                episode_count=season.episode_count,
                tmdb_id=season.tmdb_id,
            )
        )
        for episode in season.episodes:
            exists = db.scalar(
                select(MediaItem.id).where(
                    MediaItem.media_id == media.id,
                    MediaItem.season == episode.season,
                    MediaItem.episode == episode.episode,
                )
            )
            if exists is not None:
                continue
            db.add(
                MediaItem(
                    media_id=media.id,
                    season=episode.season,
                    episode=episode.episode,
                    title=episode.title,
                    air_date=episode.air_date,
                    still_url=episode.still_url,
                    tmdb_episode_id=episode.tmdb_episode_id,
                    state="missing",
                )
            )
        record_event(
            db,
            "season.announced",
            media_id=media.id,
            dedupe_key=f"season.announced:{media.id}:{season.number}",
            when=now,
            payload={
                "title": media.title,
                "media_id": media.id,
                "tmdb_id": media.tmdb_id,
                "poster_url": media.poster_url,
                "media_type": media.type,
                "season": season.number,
                "level": "info",
            },
        )
        announced.append(season.number)
    from mediatracker.core.series_follow import apply_series_status_from_tmdb

    apply_series_status_from_tmdb(db, media, fetched, now)
    from mediatracker.integrations.tmdb_enrichment import apply_tmdb_enrichment

    apply_tmdb_enrichment(media, fetched)
    media.tmdb_refreshed_at = now
    return announced


def maybe_refresh_seasons(db: Session, settings: Settings, *, now: datetime | None = None, client=None) -> int:
    """Refresh one followed series per call, at most once a day. Returns new seasons."""
    moment = now or datetime.now(timezone.utc)
    row = _next_series(db, moment)
    if row is None:
        retried = maybe_retry_pending_identify(db, settings, now=moment, client=client)
        if retried:
            return retried
        return maybe_refresh_films(db, settings, now=moment, client=client)
    if client is None:
        client = _open(db, settings)
    if client is None:
        return 0
    try:
        fetched = client.fetch(row.tmdb_id, "series")
    except TmdbError:
        row.tmdb_refreshed_at = moment
        db.commit()
        return 0
    found = apply_new_seasons(db, row, fetched, moment)
    from mediatracker.core.series_follow import stop_following_complete_series

    stop_following_complete_series(db, row, today=moment.date())
    db.commit()
    return len(found)


def maybe_refresh_films(db: Session, settings: Settings, *, now: datetime | None = None, client=None) -> int:
    """Refresh one followed film per call (availability dates), at most once a day."""
    moment = now or datetime.now(timezone.utc)
    row = _next_film(db, moment)
    if row is None:
        return maybe_refresh_unfollowed_enrichment(db, settings, now=moment, client=client)
    if client is None:
        client = _open(db, settings)
    if client is None or row.tmdb_id is None:
        return 0
    try:
        fetched = client.fetch(row.tmdb_id, "film")
    except TmdbError:
        row.tmdb_refreshed_at = moment
        db.commit()
        return 0
    apply_new_seasons(db, row, fetched, moment)
    db.commit()
    return 1


def maybe_refresh_unfollowed_enrichment(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client=None,
) -> int:
    """Enrich one non-followed fiche from TMDB, at most once a week."""
    moment = now or datetime.now(timezone.utc)
    row = _next_unfollowed(db, moment)
    if row is None:
        return 0
    if client is None:
        client = _open(db, settings)
    if client is None or row.tmdb_id is None:
        return 0
    try:
        fetched = client.fetch(row.tmdb_id, row.type)
    except TmdbError:
        row.tmdb_refreshed_at = moment
        db.commit()
        return 0
    from mediatracker.integrations.tmdb_enrichment import apply_tmdb_enrichment

    apply_tmdb_enrichment(row, fetched)
    if row.type == "film":
        from mediatracker.core.film_availability import apply_film_from_tmdb

        apply_film_from_tmdb(db, row, fetched)
    row.tmdb_refreshed_at = moment
    db.commit()
    return 1


def _next_unfollowed(db: Session, now: datetime) -> Media | None:
    cutoff = now - REFRESH_UNFOLLOWED_EVERY
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    cutoff_naive = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    cursor = _int_setting(db, _UNFOLLOWED_CURSOR)
    row = db.scalar(
        select(Media)
        .where(
            Media.followed.is_(False),
            Media.tmdb_id.is_not(None),
            or_(Media.tmdb_refreshed_at.is_(None), Media.tmdb_refreshed_at <= cutoff_naive),
            Media.id > cursor,
        )
        .order_by(Media.id)
        .limit(1)
    )
    if row is None and cursor > 0:
        row = db.scalar(
            select(Media)
            .where(
                Media.followed.is_(False),
                Media.tmdb_id.is_not(None),
                or_(Media.tmdb_refreshed_at.is_(None), Media.tmdb_refreshed_at <= cutoff_naive),
            )
            .order_by(Media.id)
            .limit(1)
        )
    if row is None:
        return None
    _set_int(db, _UNFOLLOWED_CURSOR, row.id, now)
    return row


def _next_film(db: Session, now: datetime) -> Media | None:
    cutoff = (now - REFRESH_EVERY).astimezone(timezone.utc).replace(tzinfo=None)
    rows = list(
        db.scalars(
            select(Media)
            .where(
                Media.type == "film",
                Media.followed.is_(True),
                Media.on_hold.is_(False),
                Media.tmdb_id.is_not(None),
                or_(Media.tmdb_refreshed_at.is_(None), Media.tmdb_refreshed_at <= cutoff),
            )
            .order_by(Media.id)
        ).all()
    )
    due = [row for row in rows if _stale(row.tmdb_refreshed_at, now)]
    if not due:
        return None
    cursor = _int_setting(db, _FILM_CURSOR)
    ordered = sorted(due, key=lambda row: (row.id <= cursor, row.id))
    chosen = ordered[0]
    _set_int(db, _FILM_CURSOR, chosen.id, now)
    return chosen


def _next_series(db: Session, now: datetime) -> Media | None:
    cutoff = (now - REFRESH_EVERY).astimezone(timezone.utc).replace(tzinfo=None)
    rows = list(
        db.scalars(
            select(Media)
            .where(
                Media.type == "series",
                Media.followed.is_(True),
                Media.on_hold.is_(False),
                Media.tmdb_id.is_not(None),
                or_(Media.tmdb_refreshed_at.is_(None), Media.tmdb_refreshed_at <= cutoff),
            )
            .order_by(Media.id)
        ).all()
    )
    due = [row for row in rows if _stale(row.tmdb_refreshed_at, now)]
    if not due:
        return None
    cursor = _int_setting(db, _CURSOR)
    ordered = sorted(due, key=lambda row: (row.id <= cursor, row.id))
    chosen = ordered[0]
    _set_int(db, _CURSOR, chosen.id, now)
    return chosen


def _stale(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return True
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return now - value >= REFRESH_EVERY


def _open(db: Session, settings: Settings):
    from mediatracker.core.tmdb.client import open_client

    client, _owned = open_client(db, settings)
    return client


_IDENTIFY_BATCH = 3


def _identify_backoff_active(db: Session, now: datetime) -> bool:
    row = db.get(Setting, _IDENTIFY_BACKOFF_KEY)
    if row is None or not row.value:
        return False
    try:
        until = datetime.fromisoformat(row.value)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return now < until


def _set_identify_backoff(db: Session, now: datetime) -> None:
    until = now + _IDENTIFY_BACKOFF
    row = db.get(Setting, _IDENTIFY_BACKOFF_KEY)
    text = until.isoformat()
    if row is None:
        db.add(Setting(key=_IDENTIFY_BACKOFF_KEY, value=text, is_secret=False, updated_at=now))
    else:
        row.value = text
        row.updated_at = now


def _pending_identify_batch(db: Session, now: datetime, *, batch_size: int) -> list[Media]:
    attempt_cutoff = now - _IDENTIFY_ATTEMPT_EVERY
    attempt_naive = attempt_cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    cursor = _int_setting(db, _IDENTIFY_CURSOR)
    due = list(
        db.scalars(
            select(Media)
            .where(
                Media.tmdb_identify_status == "pending",
                Media.tmdb_id.is_(None),
                or_(
                    Media.tmdb_identify_last_attempt_at.is_(None),
                    Media.tmdb_identify_last_attempt_at <= attempt_naive,
                ),
                Media.id > cursor,
            )
            .order_by(Media.id)
            .limit(batch_size)
        ).all()
    )
    if len(due) < batch_size and cursor > 0:
        extra = list(
            db.scalars(
                select(Media)
                .where(
                    Media.tmdb_identify_status == "pending",
                    Media.tmdb_id.is_(None),
                    or_(
                        Media.tmdb_identify_last_attempt_at.is_(None),
                        Media.tmdb_identify_last_attempt_at <= attempt_naive,
                    ),
                )
                .order_by(Media.id)
                .limit(batch_size - len(due))
            ).all()
        )
        seen = {row.id for row in due}
        for row in extra:
            if row.id not in seen:
                due.append(row)
                seen.add(row.id)
    if not due:
        return []
    _set_int(db, _IDENTIFY_CURSOR, due[-1].id, now)
    return due


def maybe_retry_pending_identify(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client=None,
    batch_size: int = _IDENTIFY_BATCH,
) -> int:
    """Re-run TMDB search for a few fiches still marked « à identifier »."""
    moment = now or datetime.now(timezone.utc)
    if _identify_backoff_active(db, moment):
        return 0
    rows = _pending_identify_batch(db, moment, batch_size=batch_size)
    if not rows:
        return 0
    if client is None:
        client = _open(db, settings)
    if client is None:
        return 0
    from mediatracker.core.tmdb.apply import apply_tmdb, owner_for_tmdb
    from mediatracker.core.tmdb.identify import identify_search
    from mediatracker.integrations.tmdb import TmdbError

    resolved = 0
    for row in rows:
        row.tmdb_identify_last_attempt_at = moment
        title = (row.title or "").strip()
        if not title:
            continue
        try:
            outcome = identify_search(client, title, row.year, row.type, with_glance=False)
        except TmdbError as exc:
            if exc.code == "RATE_LIMIT":
                _set_identify_backoff(db, moment)
                db.commit()
                return resolved
            continue
        if outcome.hit is None:
            continue
        if owner_for_tmdb(db, outcome.hit.tmdb_id, row.type, exclude_media_id=row.id) is not None:
            continue
        try:
            fetched = client.fetch(outcome.hit.tmdb_id, row.type)
        except TmdbError as exc:
            if exc.code == "RATE_LIMIT":
                _set_identify_backoff(db, moment)
                db.commit()
                return resolved
            continue
        row.tmdb_id = outcome.hit.tmdb_id
        imdb_id = fetched.imdb_id
        apply_tmdb(db, row, fetched, imdb_id=imdb_id, moment=moment)
        resolved += 1
    db.commit()
    return resolved


def _int_setting(db: Session, key: str) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return 0
    try:
        return int(row.value)
    except ValueError:
        return 0


def _set_int(db: Session, key: str, value: int, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=str(value), is_secret=False, updated_at=now))
        return
    row.value = str(value)
    row.updated_at = now
