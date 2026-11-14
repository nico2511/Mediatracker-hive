"""Filter aria2's on-disk session before the daemon reloads it at startup."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.incomplete import BUSY
from mediatracker.db.models import Download
from mediatracker.providers.downloaders.aria2 import filter_aria2_session_file, session_name_targets

logger = logging.getLogger(__name__)

_DB_READ_RETRIES = 5
_DB_READ_DELAY_S = 0.05


def active_aria2_session_keys(db: Session) -> tuple[set[str], set[str]]:
    """GIDs and output names for downloads that should survive a container restart."""
    rows = db.scalars(select(Download).where(Download.state.in_(BUSY))).all()
    gids = {row.handle for row in rows if row.handle}
    names = session_name_targets(row.filename for row in rows if row.filename)
    return gids, names


def _transient_sqlite_lock(exc: SQLAlchemyError) -> bool:
    if not isinstance(exc, OperationalError):
        return False
    lowered = str(exc).casefold()
    return "locked" in lowered or "database is busy" in lowered


def _read_active_session_keys(db: Session) -> tuple[set[str], set[str]]:
    """Load active download keys, retrying briefly on transient SQLite locks."""
    last: SQLAlchemyError | None = None
    for attempt in range(_DB_READ_RETRIES):
        try:
            return active_aria2_session_keys(db)
        except SQLAlchemyError as exc:
            last = exc
            if _transient_sqlite_lock(exc) and attempt + 1 < _DB_READ_RETRIES:
                time.sleep(_DB_READ_DELAY_S)
                continue
            raise
    assert last is not None
    raise last


def _reset_unfiltered_session(session: Path, *, reason: str) -> None:
    """Empty an unreadable or unfilterable session so aria2 never reloads stale errors.

    A copy is kept as ``aria2.session.unfiltered`` when possible. Active jobs may
    still be reconciled from SQLite when the database is readable on a later start.
    """
    if not session.is_file():
        return
    backup = session.with_name(f"{session.name}.unfiltered")
    try:
        shutil.copy2(session, backup)
    except OSError as exc:
        logger.warning(
            "Session aria2 non réinitialisée (%s) : copie %s impossible (%s).",
            reason,
            backup.name,
            exc,
        )
        return
    try:
        session.write_text("", encoding="utf-8")
    except OSError as exc:
        logger.warning("Session aria2 illisible et non réinitialisable (%s).", exc)
        return
    logger.warning(
        "Session aria2 réinitialisée (%s) : aria2 repart sans entrées persistées.",
        reason,
    )


def prune_aria2_session_at_startup(db: Session, settings: Settings) -> None:
    """Drop stale session entries. Runs during init, before aria2 reads ``input-file``."""
    session = Path(settings.config_dir) / "aria2.session"
    try:
        gids, names = _read_active_session_keys(db)
    except SQLAlchemyError as exc:
        if _transient_sqlite_lock(exc):
            logger.warning(
                "Session aria2 non filtrée : base temporairement verrouillée après %d tentative(s).",
                _DB_READ_RETRIES,
            )
            return
        logger.warning("Session aria2 non filtrée : base illisible (%s).", exc.__class__.__name__)
        _reset_unfiltered_session(session, reason="base illisible")
        return
    try:
        dropped = filter_aria2_session_file(session, keep_gids=gids, keep_names=names)
    except OSError as exc:
        logger.warning("Session aria2 non filtrée : fichier illisible (%s).", exc)
        _reset_unfiltered_session(session, reason="fichier illisible")
        return
    if dropped:
        logger.info("Session aria2 filtrée : %d entrée(s) retirée(s).", dropped)
    from mediatracker.hive.download_pipeline import scrub_hive_grant_from_session_file

    scrubbed = scrub_hive_grant_from_session_file(session)
    if scrubbed:
        logger.info("Session aria2 : %d en-tête(s) grant Hive retiré(s).", scrubbed)
