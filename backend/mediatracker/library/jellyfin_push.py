"""Push a MediaTracker TMDB edit to Jellyfin, then retag the folder.

The job is idempotent: the same fiche and TMDB id is not pushed twice.
A finished push records the sent values so the next catalog read ignores that echo.
Failures retry with a backoff.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import JellyfinLibraryItem, JellyfinPushJob, Media
from mediatracker.events import record_event
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError
from mediatracker.integrations.jellyfin_library import read_scan_status
from mediatracker.library.jellyfin_align import (
    AlignError,
    build_report,
    commit_renames,
    open_client,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
STUCK_AFTER = timedelta(minutes=10)


class PushError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class PushPermanent(PushError):
    """No Jellyfin item will appear on retry (missing library row, not a network fault)."""


def enqueue_identify(
    db: Session,
    media: Media,
    now: datetime,
    *,
    previous_tmdb: int | None = None,
    retry_failed: bool = True,
) -> JellyfinPushJob | None:
    """Queue one push. A finished job for the same id is left alone."""
    if media.tmdb_id is None:
        return None
    if not _has_jellyfin_target(db, media):
        logger.info("jellyfin push ignorée fiche=%s : aucun élément Jellyfin ni fichier local.", media.id)
        return None
    key = f"{media.id}:{media.tmdb_id}"
    existing = db.scalar(select(JellyfinPushJob).where(JellyfinPushJob.idempotency_key == key))
    if existing is not None and existing.state in {"pending", "running", "done"}:
        return existing
    if existing is not None and existing.state == "failed" and not retry_failed:
        return existing
    if existing is not None and existing.state == "failed":
        existing.state = "pending"
        existing.attempts = 0
        existing.next_retry_at = now
        existing.last_error = None
        existing.finished_at = None
        existing.previous_tmdb = previous_tmdb if previous_tmdb is not None else existing.previous_tmdb
        existing.title = media.title
        existing.year = media.year
        logger.info("jellyfin push nouvelle tentative fiche=%s tmdb=%s", media.id, media.tmdb_id)
        return existing
    _supersede(db, media.id, now)
    job = JellyfinPushJob(
        media_id=media.id,
        tmdb_id=media.tmdb_id,
        previous_tmdb=previous_tmdb,
        title=media.title,
        year=media.year,
        media_type=media.type,
        state="pending",
        attempts=0,
        next_retry_at=now,
        idempotency_key=key,
        created_at=now,
    )
    db.add(job)
    db.flush()
    logger.info("jellyfin push en file fiche=%s tmdb=%s", media.id, media.tmdb_id)
    return job


def note_manual_tmdb(
    db: Session,
    media: Media,
    now: datetime,
    *,
    previous_tmdb: int | None,
    previous_title: str | None = None,
    previous_year: int | None = None,
) -> None:
    """A person changed the id. The edit time is stored and the push is queued."""
    from mediatracker.library.identity_sync import note_manual_tmdb as record

    record(
        db,
        media,
        now,
        previous_tmdb=previous_tmdb,
        previous_title=previous_title,
        previous_year=previous_year,
    )


def maybe_push_jellyfin(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client: JellyfinClient | None = None,
) -> None:
    """Run at most one due job. A library scan delays it without counting as a failure."""
    moment = now or datetime.now(timezone.utc)
    _release_stuck(db, moment)
    db.flush()
    job = _due_job(db, moment)
    if job is None:
        db.commit()
        return
    opened = open_client(db, settings)
    owns = client is None
    if client is None:
        if opened is None:
            job.next_retry_at = moment + timedelta(minutes=5)
            db.commit()
            return
        client = opened[0]
    try:
        if read_scan_status(client).scanning:
            logger.info("jellyfin push en attente : une analyse Jellyfin est en cours.")
            db.commit()
            return
        job.state = "running"
        job.attempts += 1
        job.next_retry_at = moment + STUCK_AFTER
        db.flush()
        _run(db, settings, client, job, moment)
    except PushPermanent as exc:
        _fail(db, job, moment, exc.message, permanent=True)
    except (PushError, AlignError, JellyfinError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        _fail(db, job, moment, message)
    finally:
        if owns:
            client.close()
    db.commit()


def _run(db: Session, settings: Settings, client: JellyfinClient, job: JellyfinPushJob, now: datetime) -> None:
    media = db.get(Media, job.media_id) if job.media_id is not None else None
    if media is None or media.tmdb_id != job.tmdb_id:
        job.state = "done"
        job.finished_at = now
        job.last_error = None
        logger.info("jellyfin push ignorée fiche=%s : l'identifiant a changé.", job.media_id)
        return
    item_id = _jellyfin_id(db, media, job.previous_tmdb, job=job)
    if not item_id:
        raise PushPermanent("Élément Jellyfin introuvable pour cette fiche.")
    _identify(client, item_id, media)
    client.refresh_item(item_id, replace_metadata=True)
    moves = [
        row
        for row in build_report(db, settings)["renames"]
        if row["media_id"] == media.id and not row["blocked"]
    ]
    for move in moves:
        move["jellyfin_id"] = item_id
    from mediatracker.library.identity_sync import record_push_echo

    record_push_echo(media, tmdb_id=media.tmdb_id, previous_tmdb=job.previous_tmdb, when=now)
    commit_renames(db, settings, client, moves, now)
    job.state = "done"
    job.finished_at = now
    job.last_error = None
    record_event(
        db,
        "jellyfin.identity_pushed",
        media_id=media.id,
        payload={"tmdb_id": media.tmdb_id, "title": media.title, "jellyfin_id": item_id},
        dedupe_key=f"{media.id}:{media.tmdb_id}",
        when=now,
    )
    logger.info("jellyfin push ok fiche=%s tmdb=%s jellyfin=%s", media.id, media.tmdb_id, item_id)


def _identify(client: JellyfinClient, item_id: str, media: Media) -> None:
    body = {
        "Name": media.title,
        "ProviderIds": {"Tmdb": str(media.tmdb_id)},
        "SearchProviderName": "TheMovieDb",
    }
    if media.year:
        body["ProductionYear"] = media.year
    try:
        client.apply_remote_search(item_id, body)
        return
    except JellyfinError as exc:
        logger.info("jellyfin push RemoteSearch refusé (%s), écriture des ProviderIds.", exc.code)
    client.update_item(item_id, {"ProviderIds": {"Tmdb": str(media.tmdb_id)}, "Name": media.title})


def _jellyfin_id(
    db: Session,
    media: Media,
    previous_tmdb: int | None,
    *,
    job: JellyfinPushJob | None = None,
    allow_ambiguous: bool = True,
) -> str:
    manual = (media.jellyfin_series_id or "").strip()
    if manual:
        return manual
    kinds = ("Series", "Movie") if media.type == "series" else ("Movie", "Series")
    for tmdb_id in (previous_tmdb, media.tmdb_id):
        if tmdb_id is None:
            continue
        row = db.scalar(
            select(JellyfinLibraryItem).where(
                JellyfinLibraryItem.tmdb_id == tmdb_id,
                JellyfinLibraryItem.item_type.in_(kinds),
            )
        )
        if row is not None:
            _remember_jellyfin_series_id(db, media, row.jellyfin_id)
            return row.jellyfin_id
    rows = list(db.scalars(select(JellyfinLibraryItem).where(JellyfinLibraryItem.item_type.in_(kinds))).all())
    old_title, old_year = _previous_identity(db, media, job)
    for title, year in ((media.title, media.year), (old_title, old_year)):
        if not (title or "").strip():
            continue
        match = _unique_title_match(db, media, rows, title, year, previous_tmdb=previous_tmdb)
        if match == _AMBIGUOUS:
            if allow_ambiguous:
                raise PushPermanent("Appariement Jellyfin ambigu.")
            return _AMBIGUOUS
        if match:
            _remember_jellyfin_series_id(db, media, match)
            return match
    if media.type == "series":
        folder_match = _series_folder_match(db, media, rows, previous_tmdb=previous_tmdb)
        if folder_match == _AMBIGUOUS:
            if allow_ambiguous:
                raise PushPermanent("Appariement Jellyfin ambigu.")
            return _AMBIGUOUS
        if folder_match:
            _remember_jellyfin_series_id(db, media, folder_match)
            return folder_match
    return ""


_AMBIGUOUS = "__ambiguous__"


def _previous_identity(db: Session, media: Media, job: JellyfinPushJob | None) -> tuple[str | None, int | None]:
    from mediatracker.db.models import IdentityChange

    change = db.scalar(
        select(IdentityChange)
        .where(IdentityChange.media_id == media.id, IdentityChange.source == "mediatracker")
        .order_by(IdentityChange.id.desc())
    )
    if change is not None:
        return change.old_title or None, change.old_year
    if job is not None and job.title:
        return job.title, job.year
    return None, None


def _jellyfin_row_tmdb_allowed(
    row: JellyfinLibraryItem,
    media: Media,
    previous_tmdb: int | None,
) -> bool:
    if row.tmdb_id is None:
        return True
    allowed = {tid for tid in (media.tmdb_id, previous_tmdb) if tid is not None}
    return row.tmdb_id in allowed


def _unique_title_match(
    db: Session,
    media: Media,
    rows: list[JellyfinLibraryItem],
    title: str,
    year: int | None,
    *,
    previous_tmdb: int | None,
) -> str:
    from mediatracker.library.parse import normalize_title

    wanted = normalize_title(title)
    if not wanted:
        return ""
    candidates: list[str] = []
    for row in rows:
        if media.type == "series" and row.item_type != "Series":
            continue
        if media.type == "film" and row.item_type != "Movie":
            continue
        name = normalize_title(row.name or "")
        if name != wanted:
            continue
        if not _jellyfin_row_tmdb_allowed(row, media, previous_tmdb):
            continue
        if year is not None and row.year is not None and abs(year - row.year) > 1:
            continue
        candidates.append(row.jellyfin_id)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return _AMBIGUOUS
    return ""


def _series_folder_match(
    db: Session,
    media: Media,
    rows: list[JellyfinLibraryItem],
    *,
    previous_tmdb: int | None,
) -> str:
    from mediatracker.db.models import MediaItem

    series_roots: set[str] = set()
    for item in db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all():
        if not item.file_path:
            continue
        path = Path(item.file_path)
        parent = path.parent
        if parent.name.lower().startswith("season") or parent.name.lower().startswith("saison"):
            parent = parent.parent
        series_roots.add(_path_key(parent))
    if not series_roots:
        return ""
    hits: list[str] = []
    for row in rows:
        if row.item_type != "Series" or not row.local_path:
            continue
        root = _path_key(Path(row.local_path))
        if not root:
            continue
        if not _jellyfin_row_tmdb_allowed(row, media, previous_tmdb):
            continue
        if any(ep_root == root or ep_root.startswith(root + "/") for ep_root in series_roots):
            hits.append(row.jellyfin_id)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return _AMBIGUOUS
    return ""


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold().rstrip("/")
    except OSError:
        return str(path).casefold().rstrip("/")


def _remember_jellyfin_series_id(db: Session, media: Media, jellyfin_id: str) -> None:
    if jellyfin_id and not (media.jellyfin_series_id or "").strip():
        media.jellyfin_series_id = jellyfin_id


def _has_jellyfin_target(db: Session, media: Media) -> bool:
    from mediatracker.db.models import MediaItem

    if (media.jellyfin_series_id or "").strip():
        return True
    has_file = db.scalar(
        select(MediaItem.id).where(MediaItem.media_id == media.id, MediaItem.file_path.is_not(None))
    )
    if has_file is not None:
        return True
    resolved = _jellyfin_id(db, media, None, allow_ambiguous=False)
    return resolved not in ("", None)


def _due_job(db: Session, now: datetime) -> JellyfinPushJob | None:
    rows = list(
        db.scalars(select(JellyfinPushJob).where(JellyfinPushJob.state == "pending").order_by(JellyfinPushJob.id)).all()
    )
    for row in rows:
        due = row.next_retry_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due is None or due <= now:
            return row
    return None


def _supersede(db: Session, media_id: int, now: datetime) -> None:
    rows = list(
        db.scalars(
            select(JellyfinPushJob).where(
                JellyfinPushJob.media_id == media_id,
                JellyfinPushJob.state.in_(("pending", "running")),
            )
        ).all()
    )
    for row in rows:
        row.state = "failed"
        row.last_error = "Remplacé par une correction plus récente."
        row.finished_at = now


def _release_stuck(db: Session, now: datetime) -> None:
    rows = list(db.scalars(select(JellyfinPushJob).where(JellyfinPushJob.state == "running")).all())
    for row in rows:
        due = row.next_retry_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due is None or due <= now:
            row.state = "pending"
            row.next_retry_at = now
            logger.info("jellyfin push reprise fiche=%s après un arrêt.", row.media_id)


def _fail(db: Session, job: JellyfinPushJob, now: datetime, message: str, *, permanent: bool = False) -> None:
    job.last_error = message[:500]
    if permanent:
        job.state = "skipped"
        job.finished_at = now
        logger.warning("jellyfin push abandonnée fiche=%s : %s", job.media_id, message)
        return
    if job.attempts >= MAX_ATTEMPTS:
        job.state = "failed"
        job.finished_at = now
        logger.warning("jellyfin push échec fiche=%s : %s", job.media_id, message)
        return
    job.state = "pending"
    delay = 30 * (2 ** max(job.attempts - 1, 0))
    job.next_retry_at = now + timedelta(seconds=min(3600, delay))
    logger.info("jellyfin push reportée essai=%s : %s", job.attempts, message)
