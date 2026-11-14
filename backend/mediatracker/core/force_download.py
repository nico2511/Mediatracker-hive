"""Queue an episode again from the fiche, past the automatic guards.

A job already in progress is left alone. A file already on disk is reported
first; the download is created only after the user confirms.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.download_guard import DOWNLOAD_IN_PROGRESS as IN_PROGRESS
from mediatracker.core.pipeline.download_guard import active_download_for_item

__all__ = ["IN_PROGRESS", "ForceResult", "LocalFile", "force_downloads"]
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.events import record_event


@dataclass(frozen=True)
class LocalFile:
    item: MediaItem
    path: str


@dataclass(frozen=True)
class ForceResult:
    queued: tuple[Download, ...]
    busy: tuple[MediaItem, ...]
    local: tuple[LocalFile, ...]
    message: str

    @property
    def needs_confirm(self) -> bool:
        return bool(self.local)


def force_downloads(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    *,
    confirm: bool,
    now: datetime,
) -> ForceResult:
    """Create one pending job per episode. Busy rows and unconfirmed local files stay out."""
    from mediatracker.core.source_poll import refresh_library_claims

    refresh_library_claims(db, media, items, settings, now)
    queued: list[Download] = []
    queued_items: list[MediaItem] = []
    busy: list[MediaItem] = []
    local: list[LocalFile] = []
    for item in items:
        if _in_progress(db, item.id):
            busy.append(item)
            continue
        found = _local_path(db, settings, media, item)
        if found is not None and not confirm:
            local.append(LocalFile(item, found))
            continue
        row = Download(
            media_item_id=item.id,
            state="pending",
            forced=found is not None,
            created_at=now,
        )
        db.add(row)
        db.flush()
        _revive_links(db, item.id, now=now)
        record_event(
            db,
            "download.forced",
            media_id=media.id,
            item_id=item.id,
            payload={
                "title": media.title,
                "media_id": media.id,
                "item_id": item.id,
                "download_id": row.id,
                "season": item.season,
                "episode": item.episode,
                "watched": bool(item.watched),
                "local_path": found,
                "level": "info",
            },
        )
        queued.append(row)
        queued_items.append(item)
    held = bool(media.on_hold) and any(not row.forced for row in queued)
    if queued:
        from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker

        wake_worker(db, settings, REASON_API_MEDIA, commit=False)
    return ForceResult(
        tuple(queued),
        tuple(busy),
        tuple(local),
        _message(queued_items, busy, local, held=held),
    )


def _in_progress(db: Session, item_id: int) -> bool:
    return active_download_for_item(db, item_id) is not None


def _local_path(db: Session, settings: Settings, media: Media, item: MediaItem) -> str | None:
    from mediatracker.core.library_video import find_episode_on_disk
    from mediatracker.core.source_poll import _path_present
    from mediatracker.core.storage import library_skip, storage_layout

    if item.file_path and _path_present(item.file_path):
        return item.file_path
    layout = storage_layout(db, settings)
    found = find_episode_on_disk(
        movies_dir=layout.movies_dir,
        series_dir=layout.series_dir,
        media_type=media.type,
        title=media.title,
        year=media.year,
        season=item.season,
        episode=item.episode,
        tmdb_id=media.tmdb_id,
        skip=library_skip(layout),
    )
    if found is None:
        return None
    return str(found)


def _revive_links(db: Session, item_id: int, *, now: datetime) -> None:
    """Revive dead links allowed by the retry memory rules (F8)."""
    from mediatracker.core.dead_links import revive_links_for_retry

    revive_links_for_retry(db, item_id, now)


def _message(
    queued: list[MediaItem],
    busy: list[MediaItem],
    local: list[LocalFile],
    *,
    held: bool,
) -> str:
    parts: list[str] = []
    if queued:
        labels = _join(_label(item) for item in queued)
        verb = "est mis en file." if len(queued) == 1 else "sont mis en file."
        parts.append(f"{labels} {verb}")
    if local:
        names = ", ".join(Path(hit.path).name for hit in local)
        labels = _join(_label(hit.item) for hit in local)
        parts.append(f"{labels} déjà sur disque ({names}). Confirmez pour télécharger quand même.")
    if busy:
        labels = _join(_label(item) for item in busy)
        parts.append(f"{labels} : un téléchargement est déjà en cours.")
    if not parts:
        return "Rien à mettre en file."
    if held:
        parts.append("La série est en pause : le démarrage attend la reprise.")
    return " ".join(parts)


def _label(item: MediaItem) -> str:
    if item.season is None or item.episode is None:
        return "Film"
    return f"S{item.season:02d}E{item.episode:02d}"


def _join(labels) -> str:
    names = list(labels)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} et {names[1]}"
    return ", ".join(names[:-1]) + f" et {names[-1]}"
