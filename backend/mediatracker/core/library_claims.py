"""Align episode file claims with the library index after a scan."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.source_poll import refresh_library_claims
from mediatracker.db.models import LibraryFile, Media, MediaItem


def media_ids_touched_by_scan(db: Session, moment: datetime) -> set[int]:
    """Media fiches whose library rows changed at ``moment`` (scan timestamp)."""
    rows = db.scalars(
        select(LibraryFile.media_id).where(
            LibraryFile.media_id.is_not(None),
            or_(LibraryFile.seen_at == moment, LibraryFile.missing_at == moment),
        )
    ).all()
    return {media_id for media_id in rows if media_id is not None}


def reconcile_library_claims_after_scan(db: Session, settings: Settings, moment: datetime) -> bool:
    """Refresh downloaded flags for fiches touched by the scan. Does not commit."""
    media_ids = media_ids_touched_by_scan(db, moment)
    if not media_ids:
        return False
    changed = False
    for media_id in sorted(media_ids):
        media = db.get(Media, media_id)
        if media is None:
            continue
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media_id)).all())
        if refresh_library_claims(db, media, items, settings, moment):
            changed = True
    return changed
