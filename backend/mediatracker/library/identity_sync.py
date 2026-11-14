"""Last write wins for a fiche's TMDB id.

MediaTracker records the time of its own edit. Jellyfin's clock is
``DateLastSaved`` on the series or movie. The newer clock keeps the id.
A value this app just pushed is an echo: it is stored with the push time and
ignored, including the previous id while Jellyfin is still catching up.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import IdentityChange, JellyfinPushJob, Media, Setting

logger = logging.getLogger(__name__)

IDENTITY_DUE = "jellyfin.identity_due"
# Jellyfin can still show the id from before our push while its refresh lands.
ECHO_GRACE = timedelta(minutes=2)


class IdentityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def classify(
    *,
    media_tmdb: int | None,
    edited_at: datetime | None,
    echo_tmdb: int | None,
    echo_previous: int | None,
    echo_at: datetime | None,
    jellyfin_tmdb: int | None,
    jellyfin_saved: datetime | None,
) -> str:
    """``agree``, ``echo``, ``push`` (MediaTracker is newer), or ``adopt`` (Jellyfin is newer)."""
    if jellyfin_tmdb is None or jellyfin_tmdb == media_tmdb:
        return "agree"
    saved = _aware(jellyfin_saved)
    echoed = _aware(echo_at)
    if echo_tmdb is not None and jellyfin_tmdb == echo_tmdb:
        return "echo"
    if echo_previous is not None and jellyfin_tmdb == echo_previous and echoed is not None:
        if saved is None or saved <= echoed + ECHO_GRACE:
            return "echo"
    if echoed is not None and saved is not None and saved <= echoed:
        return "echo"
    edited = _aware(edited_at)
    if edited is not None and (saved is None or saved <= edited):
        return "push"
    return "adopt"


def note_manual_tmdb(
    db: Session,
    media: Media,
    now: datetime,
    *,
    previous_tmdb: int | None,
    previous_title: str | None = None,
    previous_year: int | None = None,
) -> None:
    """A person changed the id. That clock is MediaTracker's, and Jellyfin is queued."""
    journal(
        db,
        media,
        source="mediatracker",
        new_tmdb=media.tmdb_id,
        new_title=media.title,
        new_year=media.year,
        old_tmdb=previous_tmdb,
        old_title=(previous_title or media.title)[:300],
        old_year=previous_year if previous_year is not None else media.year,
        when=now,
    )
    media.identity_edited_at = now
    _clear_echo(media)
    if media.tmdb_id is None:
        cancel_pending_pushes(db, media.id, now, "Identifiant retiré.")
        return
    from mediatracker.library.jellyfin_push import enqueue_identify

    enqueue_identify(db, media, now, previous_tmdb=previous_tmdb)


def record_push_echo(media: Media, *, tmdb_id: int, previous_tmdb: int | None, when: datetime) -> None:
    """Remember what we sent so the next Jellyfin read of those values is not a new edit."""
    media.identity_echo_tmdb = tmdb_id
    media.identity_echo_previous = previous_tmdb
    media.identity_echo_at = when


def adopt(
    db: Session,
    media: Media,
    *,
    tmdb_id: int,
    title: str | None,
    year: int | None,
    when: datetime,
) -> bool:
    """Take Jellyfin's id. The MediaTracker edit clock stays where the person left it."""
    if media.tmdb_id == tmdb_id:
        return False
    new_title = (title or media.title)[:300]
    new_year = year if year is not None else media.year
    journal(
        db,
        media,
        source="jellyfin",
        new_tmdb=tmdb_id,
        new_title=new_title,
        new_year=new_year,
        old_tmdb=media.tmdb_id,
        old_title=media.title,
        old_year=media.year,
        when=when,
    )
    media.tmdb_id = tmdb_id
    media.title = new_title
    media.year = new_year
    _clear_echo(media)
    cancel_pending_pushes(db, media.id, when, "Identifiant repris depuis Jellyfin.")
    logger.info("Fiche %s reprend le TMDB %s de Jellyfin.", media.id, tmdb_id)
    return True


def undo(db: Session, media: Media, change_id: int, now: datetime) -> IdentityChange:
    """Reverse one journal row. The reversal is itself a MediaTracker write and is pushed."""
    row = db.get(IdentityChange, change_id)
    if row is None or row.media_id != media.id:
        raise IdentityError("NOT_FOUND", "Modification introuvable.")
    if row.undone_at is not None:
        raise IdentityError("UNDONE", "Cette modification est déjà annulée.")
    if row.old_tmdb is not None:
        clash = db.scalar(
            select(Media).where(Media.tmdb_id == row.old_tmdb, Media.type == media.type, Media.id != media.id)
        )
        if clash is not None:
            raise IdentityError("CLASH", "Une autre fiche porte déjà cet identifiant TMDB.")
    previous = media.tmdb_id
    journal(
        db,
        media,
        source="mediatracker",
        new_tmdb=row.old_tmdb,
        new_title=row.old_title or media.title,
        new_year=row.old_year,
        old_tmdb=previous,
        old_title=media.title,
        old_year=media.year,
        when=now,
    )
    media.tmdb_id = row.old_tmdb
    if row.old_title:
        media.title = row.old_title
    media.year = row.old_year
    media.identity_edited_at = now
    _clear_echo(media)
    row.undone_at = now
    cancel_pending_pushes(db, media.id, now, "Annulé depuis le journal.")
    if media.tmdb_id is not None:
        from mediatracker.library.jellyfin_push import enqueue_identify

        enqueue_identify(db, media, now, previous_tmdb=previous)
    logger.info("Journal %s annulé pour la fiche %s.", row.id, media.id)
    return row


def schedule_identity_read(db: Session, now: datetime) -> None:
    """Ask the worker for one incremental catalog read. The webhook body is not stored."""
    row = db.get(Setting, IDENTITY_DUE)
    if row is None:
        db.add(Setting(key=IDENTITY_DUE, value="1", is_secret=False, updated_at=now))
        return
    row.value = "1"
    row.updated_at = now


def journal(
    db: Session,
    media: Media,
    *,
    source: str,
    new_tmdb: int | None,
    new_title: str,
    new_year: int | None,
    old_tmdb: int | None,
    old_title: str,
    old_year: int | None,
    when: datetime,
) -> IdentityChange:
    row = IdentityChange(
        media_id=media.id,
        source=source,
        old_tmdb=old_tmdb,
        new_tmdb=new_tmdb,
        old_title=(old_title or "")[:300],
        new_title=(new_title or "")[:300],
        old_year=old_year,
        new_year=new_year,
        changed_at=when,
    )
    db.add(row)
    db.flush()
    return row


def cancel_pending_pushes(db: Session, media_id: int, now: datetime, reason: str) -> None:
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
        row.last_error = reason[:500]
        row.finished_at = now


def _clear_echo(media: Media) -> None:
    media.identity_echo_tmdb = None
    media.identity_echo_previous = None
    media.identity_echo_at = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
