"""Targeted library re-scan for one fiche or season."""

from __future__ import annotations

import threading
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.library_video import canonical_folders_for_media
from mediatracker.core.storage import library_on_local_disk, library_skip, storage_layout
from mediatracker.core.tmdb import open_client
from mediatracker.db.models import Media, MediaItem
from mediatracker.library.scanner import TargetedScanReport, scan_folders

_registry_guard = threading.Lock()
_active: dict[int, threading.Lock] = {}


class ReanalyzeBusy(Exception):
    """Another reanalyze is already running for this fiche."""


def _lock_for(media_id: int) -> threading.Lock:
    with _registry_guard:
        lock = _active.get(media_id)
        if lock is None:
            lock = threading.Lock()
            _active[media_id] = lock
        return lock


def reanalyze_media(
    db: Session,
    settings: Settings,
    media: Media,
    *,
    season: int | None = None,
    now: datetime | None = None,
) -> TargetedScanReport:
    """Scan canonical folders for this fiche. One run at a time per media id."""
    lock = _lock_for(media.id)
    if not lock.acquire(blocking=False):
        raise ReanalyzeBusy()
    try:
        return _run(db, settings, media, season=season, now=now)
    finally:
        lock.release()


def _run(
    db: Session,
    settings: Settings,
    media: Media,
    *,
    season: int | None,
    now: datetime | None,
) -> TargetedScanReport:
    layout = storage_layout(db, settings)
    if not library_on_local_disk(db, settings):
        from mediatracker.library.scanner import scan_library

        client, owned = open_client(db, settings)
        skip = library_skip(layout)
        try:
            report = scan_library(
                db,
                movies=layout.movies_dir,
                series=layout.series_dir,
                now=now,
                tmdb=client,
                skip=skip,
                settings=settings,
            )
        finally:
            if owned and client is not None:
                client.close()
        return TargetedScanReport(found=report.seen, new=report.matched, absent=report.missing)

    seasons: list[int] | None
    if media.type == "film":
        seasons = None
    elif season is not None:
        seasons = [season]
    else:
        seasons = sorted(
            {
                item.season
                for item in db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all()
                if item.season is not None
            }
        )
    folders = tuple(
        canonical_folders_for_media(
            movies_dir=layout.movies_dir,
            series_dir=layout.series_dir,
            media_type=media.type,
            title=media.title,
            year=media.year,
            tmdb_id=media.tmdb_id,
            seasons=seasons,
        )
    )
    client, owned = open_client(db, settings)
    skip = library_skip(layout)
    try:
        report = scan_folders(
            db,
            folders=folders,
            movies=layout.movies_dir,
            series=layout.series_dir,
            now=now,
            tmdb=client,
            skip=skip,
            settings=settings,
        )
    finally:
        if owned and client is not None:
            client.close()
    return report
