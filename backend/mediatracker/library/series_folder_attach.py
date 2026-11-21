"""Attach on-disk videos from the show folder when episode slots lack a file path.

Reuses ``own_series_folder`` and ``_attach_tree`` from the library repair path.
Nothing is moved, renamed, or detached.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, MediaItem

SERIES_FOLDER_ATTACH_COOLDOWN = timedelta(minutes=15)

logger = logging.getLogger(__name__)

_last_pass_at: dict[int, datetime] = {}
_pass_locks: dict[int, threading.Lock] = {}
_meta_lock = threading.Lock()


def reset_series_folder_attach_for_tests() -> None:
    """Clear per-fiche throttle. Tests call this between cases."""
    with _meta_lock:
        _last_pass_at.clear()
        _pass_locks.clear()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lock_for(media_id: int) -> threading.Lock:
    with _meta_lock:
        return _pass_locks.setdefault(media_id, threading.Lock())


def series_has_unfiled_slots(items: list[MediaItem]) -> bool:
    return any(
        item.season is not None and item.episode is not None and not (item.file_path or "").strip()
        for item in items
    )


def _unique_series_folder(series_root: Path, media: Media) -> Path | None:
    """Like ``own_series_folder``, but skip a title-only match when several folders qualify."""
    from mediatracker.core.library_plan import _folder_title, _tag
    from mediatracker.library.identity import folder_matches_media, own_series_folder

    own = own_series_folder(series_root, media)
    if own is None:
        return None
    tag = _tag(own.name)
    if media.tmdb_id is not None and tag == media.tmdb_id:
        return own
    matches = 0
    for child in series_root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        title, year = _folder_title(child.name)
        if folder_matches_media(title, year, _tag(child.name), media):
            matches += 1
            if matches > 1:
                logger.info(
                    "Rattachement du dossier série ignoré : plusieurs dossiers correspondent à la fiche %s.",
                    media.id,
                )
                return None
    return own


def attach_series_folder_if_needed(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    now: datetime,
    *,
    force: bool = False,
) -> int:
    """Walk the series folder tree when slots still lack ``file_path``. Returns files linked."""
    if media.type != "series" or not series_has_unfiled_slots(items):
        return 0
    if media.id is not None:
        with _lock_for(media.id):
            if not force:
                last = _last_pass_at.get(media.id)
                if last is not None and _aware_utc(now) - _aware_utc(last) < SERIES_FOLDER_ATTACH_COOLDOWN:
                    return 0
            _last_pass_at[media.id] = _aware_utc(now)
    from mediatracker.library.scanner import _attach_tree, library_roots

    try:
        _movies, series_root = library_roots(db, settings)
        own = _unique_series_folder(series_root, media)
        if own is None:
            return 0
        return _attach_tree(db, media, own, series_root, now, bound=own, settings=settings)
    except OSError:
        logger.exception("Rattachement du dossier série échoué (fiche %s).", media.id)
        return 0
