"""Integrity gate when a download is filed into the library."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.core.video_integrity import integrity_issue
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.events import record_event


def block_if_suspect(
    db: Session,
    row: Download,
    item: MediaItem,
    media: Media,
    source: Path,
    now: datetime,
) -> bool:
    """Return True when filing must stop and the file stays on disk."""
    reason = integrity_issue(source)
    if reason is None:
        return False
    _mark_suspect(db, source, media, item, reason, now, download_id=row.id)
    from mediatracker.core.pipeline.jobs import _fail

    _fail(
        db,
        row,
        "FILE_INTEGRITY",
        f"Fichier suspect : {reason} Le fichier n'a pas été rangé.",
        now,
    )
    return True


def check_source_before_move(
    db: Session,
    source: Path,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> bool:
    """Used when replacing a library copy. Returns True if the move must be aborted."""
    reason = integrity_issue(source)
    if reason is None:
        return False
    _mark_suspect(db, source, media, item, reason, now, download_id=None)
    return True


def _mark_suspect(
    db: Session,
    source: Path,
    media: Media,
    item: MediaItem,
    reason: str,
    now: datetime,
    *,
    download_id: int | None,
) -> None:
    from mediatracker.library.parse import parse_path
    from mediatracker.library.scanner import _upsert_row

    parsed = parse_path(source.resolve(), root=source.parent, root_kind=media.type)
    row = _upsert_row(db, source, parsed, now)
    row.integrity_suspect = True
    row.media_id = media.id
    row.media_item_id = item.id
    row.tmdb_id = media.tmdb_id
    row.note = reason[:500]
    record_event(
        db,
        "library.file_suspect",
        level="warning",
        media_id=media.id,
        item_id=item.id,
        dedupe_key=f"library.file_suspect:{row.id}:{int(now.timestamp())}",
        payload={
            "title": media.title,
            "media_id": media.id,
            "item_id": item.id,
            "path": str(source.resolve()),
            "message": reason,
            "download_id": download_id,
            "level": "warning",
        },
        when=now,
    )
