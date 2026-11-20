"""Attach library files after follow without holding the API SQLite transaction."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from mediatracker.config import Settings
from mediatracker.db.session import commit_retry, release_sqlite_before_network

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending: set[threading.Thread] = set()


def reset_for_tests() -> None:
    """Drop pending thread handles. Tests call this between cases."""
    with _lock:
        _pending.clear()


def join_pending(*, timeout: float = 30.0) -> None:
    """Wait for scheduled attach jobs (tests)."""
    with _lock:
        threads = list(_pending)
    for thread in threads:
        thread.join(timeout=timeout)
    with _lock:
        for thread in threads:
            _pending.discard(thread)


def schedule_attach_known_files(factory: sessionmaker, settings: Settings, media_id: int) -> None:
    """Start one attach pass for a fiche. Returns before Jellyfin or disk scan."""
    thread = threading.Thread(
        target=_run,
        args=(factory, settings, media_id),
        name=f"attach-known-{media_id}",
        daemon=True,
    )
    with _lock:
        _pending.add(thread)
    thread.start()


def _run(factory: sessionmaker, settings: Settings, media_id: int) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import select

    from mediatracker.db.models import Media, MediaItem
    from mediatracker.library.scanner import attach_known_files
    from mediatracker.library.series_folder_attach import attach_series_folder_if_needed

    db = factory()
    try:
        media = db.get(Media, media_id)
        if media is None or not media.followed:
            return
        release_sqlite_before_network(db)
        now = datetime.now(timezone.utc)
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media_id)).all())
        attached = attach_series_folder_if_needed(db, settings, media, items, now, force=True)
        attached += attach_known_files(db, settings, media)
        if attached:
            commit_retry(db)
    except Exception:
        logger.exception("Rattachement bibliothèque différé échoué (fiche %s).", media_id)
        db.rollback()
    finally:
        db.close()


def commit_follow_side_effects(db: Session, settings: Settings, *, wake: bool = True) -> None:
    """Persist follow signals and worker wake before any slow attach work."""
    if wake:
        from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker

        wake_worker(db, settings, REASON_API_MEDIA, commit=False)
    db.commit()