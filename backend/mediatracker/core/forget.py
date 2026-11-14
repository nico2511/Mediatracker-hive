"""Clear a local file claim and record why it left the disk.

Voluntary removal (manual or automatic trash) must not be auto-downloaded again.
An unexpected disappearance stays offered for re-download.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.library_guard import absence_updates_allowed
from mediatracker.core.library_video import valid_video
from mediatracker.core.storage import is_under
from mediatracker.db.models import LibraryFile, Media, MediaItem

TRACE_DELETED_VOLUNTARILY = "supprimé_volontairement"
TRACE_FILE_ABSENT = "fichier_absent"
_FORGET_TRACES = frozenset({TRACE_DELETED_VOLUNTARILY, TRACE_FILE_ABSENT})


def is_voluntarily_deleted(item: MediaItem) -> bool:
    return _has_trace(item, TRACE_DELETED_VOLUNTARILY)


def is_file_absent(item: MediaItem) -> bool:
    return _has_trace(item, TRACE_FILE_ABSENT)


def clear_forget_traces(item: MediaItem) -> None:
    traces = _trace_list(item)
    kept = [entry for entry in traces if entry.get("kind") not in _FORGET_TRACES]
    if len(kept) != len(traces):
        item.traces = kept


def forget_local_file(
    db: Session,
    media: Media | None,
    item: MediaItem,
    now: datetime,
    *,
    voluntary: bool = False,
    skip: tuple[Path, ...] = (),
    old_path: str | None = None,
    update_library: bool = True,
) -> None:
    """Drop downloaded fields and align library rows with why the file is gone."""
    if not voluntary and not absence_updates_allowed(db):
        return
    path_before = old_path if old_path is not None else item.file_path
    if update_library and item.id is not None:
        _sync_library_missing(db, media, item, now, skip=skip, old_path=path_before)
    if not item.downloaded and not item.file_path:
        if voluntary:
            _add_trace(item, TRACE_DELETED_VOLUNTARILY, now)
        return
    item.downloaded = False
    item.downloaded_at = None
    item.file_path = None
    if voluntary:
        _add_trace(item, TRACE_DELETED_VOLUNTARILY, now)
        if item.state in {"downloaded", "watched"}:
            item.state = "available" if item.source_available else "missing"
    else:
        if item.state == "downloaded":
            item.state = "missing"
        if path_before:
            _add_trace(item, TRACE_FILE_ABSENT, now)
            _clear_obtained_poll(media)


def _clear_obtained_poll(media: Media | None) -> None:
    """A film marked absent is no longer « obtenu » for poll UI and source checks."""
    if media is None or media.type != "film":
        return
    if media.last_poll_result == "obtained":
        media.last_poll_result = None
        media.last_poll_message = None


def _sync_library_missing(
    db: Session,
    media: Media | None,
    item: MediaItem,
    now: datetime,
    *,
    skip: tuple[Path, ...],
    old_path: str | None,
) -> None:
    rows = list(db.scalars(select(LibraryFile).where(LibraryFile.media_item_id == item.id)).all())
    if old_path:
        extra = db.scalar(select(LibraryFile).where(LibraryFile.path == old_path))
        if extra is not None and extra not in rows:
            rows.append(extra)
    touched = False
    for row in rows:
        if row.status == "trashed":
            continue
        if not _library_row_missing(row, skip):
            continue
        row.status = "missing"
        row.missing_at = now
        if row.media_item_id is None:
            row.media_item_id = item.id
        touched = True
    if old_path and not touched and media is not None:
        orphan = db.scalar(select(LibraryFile).where(LibraryFile.path == old_path))
        if orphan is None:
            db.add(
                LibraryFile(
                    path=old_path,
                    parsed_title=(item.title or media.title or "")[:300],
                    media_type=media.type if media.type in {"film", "series"} else "series",
                    media_id=media.id,
                    media_item_id=item.id,
                    season=item.season,
                    episode=item.episode,
                    status="missing",
                    seen_at=now,
                    missing_at=now,
                )
            )
        elif orphan.status != "trashed":
            orphan.status = "missing"
            orphan.missing_at = now
            if orphan.media_item_id is None:
                orphan.media_item_id = item.id


def _library_row_missing(row: LibraryFile, skip: tuple[Path, ...]) -> bool:
    if row.status in {"missing", "trashed"}:
        return False
    path = Path(row.path) if row.path else None
    if path is None:
        return True
    if skip and is_under(path, skip):
        return True
    return not valid_video(path)


def _has_trace(item: MediaItem, kind: str) -> bool:
    return any(entry.get("kind") == kind for entry in _trace_list(item))


def _add_trace(item: MediaItem, kind: str, now: datetime) -> None:
    traces = [entry for entry in _trace_list(item) if entry.get("kind") != kind]
    traces.append({"kind": kind, "at": now.isoformat()})
    item.traces = traces


def _trace_list(item: MediaItem) -> list[dict]:
    raw = item.traces
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]
