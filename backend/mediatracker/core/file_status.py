"""Read-time file status from library rows, trash, downloads, and traces.

No per-item disk access. Callers load one index per request to avoid N+1 queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.force_download import IN_PROGRESS
from mediatracker.core.forget import is_file_absent, is_voluntarily_deleted
from mediatracker.db.models import Download, LibraryFile, MediaItem, TrashEntry

FileStatus = Literal["present", "trash", "absent", "downloading"]


@dataclass
class FileStatusCounts:
    present: int = 0
    trash: int = 0
    absent: int = 0
    downloading: int = 0

    def total(self) -> int:
        return self.present + self.trash + self.absent + self.downloading


def aggregate_file_status(counts: FileStatusCounts) -> FileStatus:
    """Pick one season-level status when episodes disagree."""
    if counts.total() == 0:
        return "absent"
    if counts.downloading > 0:
        return "downloading"
    if counts.present == counts.total():
        return "present"
    if counts.trash == counts.total():
        return "trash"
    if counts.absent == counts.total():
        return "absent"
    if counts.present > 0:
        return "present"
    if counts.trash > 0:
        return "trash"
    return "absent"


class FileStatusIndex:
    """Batched lookup built once per media or media page."""

    def __init__(
        self,
        *,
        present: set[int],
        trashed: set[int],
        downloading: set[int],
        items: dict[int, MediaItem],
        trash_entry_id: dict[int, int],
    ) -> None:
        self._present = present
        self._trashed = trashed
        self._downloading = downloading
        self._items = items
        self._trash_entry_id = trash_entry_id

    @classmethod
    def load(cls, db: Session, items: list[MediaItem]) -> FileStatusIndex:
        ids = [item.id for item in items if item.id is not None]
        by_id = {item.id: item for item in items if item.id is not None}
        if not ids:
            return cls(present=set(), trashed=set(), downloading=set(), items={}, trash_entry_id={})
        present = set(
            db.scalars(
                select(LibraryFile.media_item_id).where(
                    LibraryFile.media_item_id.in_(ids),
                    LibraryFile.status == "matched",
                    LibraryFile.missing_at.is_(None),
                )
            ).all()
        )
        trashed_files = set(
            db.scalars(
                select(LibraryFile.media_item_id).where(
                    LibraryFile.media_item_id.in_(ids),
                    LibraryFile.status == "trashed",
                )
            ).all()
        )
        active_trash_rows = list(
            db.scalars(
                select(TrashEntry).where(
                    TrashEntry.media_item_id.in_(ids),
                    TrashEntry.restored_at.is_(None),
                    TrashEntry.purged_at.is_(None),
                )
            ).all()
        )
        trashed_entries = {row.media_item_id for row in active_trash_rows if row.media_item_id is not None}
        trash_entry_id = {
            row.media_item_id: row.id
            for row in active_trash_rows
            if row.media_item_id is not None
        }
        downloading = set(
            db.scalars(
                select(Download.media_item_id).where(
                    Download.media_item_id.in_(ids),
                    Download.state.in_(IN_PROGRESS),
                )
            ).all()
        )
        voluntary = {item_id for item_id, item in by_id.items() if is_voluntarily_deleted(item)}
        trashed = trashed_files | trashed_entries | voluntary
        return cls(
            present=present,
            trashed=trashed,
            downloading=downloading,
            items=by_id,
            trash_entry_id=trash_entry_id,
        )

    def trash_entry_for(self, item_id: int) -> int | None:
        return self._trash_entry_id.get(item_id)

    def for_item(self, item: MediaItem) -> FileStatus:
        item_id = item.id
        if item_id is None:
            return "absent"
        if item_id in self._downloading:
            return "downloading"
        if item_id in self._present:
            return "present"
        if item_id in self._trashed:
            return "trash"
        if is_file_absent(item):
            return "absent"
        return "absent"

    def counts_for(self, episodes: list[MediaItem]) -> FileStatusCounts:
        counts = FileStatusCounts()
        for item in episodes:
            status = self.for_item(item)
            if status == "present":
                counts.present += 1
            elif status == "trash":
                counts.trash += 1
            elif status == "downloading":
                counts.downloading += 1
            else:
                counts.absent += 1
        return counts
