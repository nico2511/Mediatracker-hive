"""An episode is on disk only when a real video file is still there.

A downloaded flag, a scanner row, a Jellyfin cache entry, a subtitle, an
``.nfo`` or a ``-thumb.jpg`` do not count. Copies under the download folder
or the trash do not count either.
"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.forget import clear_forget_traces, forget_local_file, is_file_absent
from mediatracker.core.library_guard import absence_updates_allowed
from mediatracker.core.library_video import find_episode_on_disk, valid_video
from mediatracker.core.storage import StorageLayout, is_under, library_skip, storage_layout
from mediatracker.db.models import LibraryFile, Media, MediaItem

_CLAIMED = {"missing", "available", "queued", "downloading", "pending", "failed"}


def local_video(db: Session, settings: Settings, item: MediaItem, now) -> Path | None:
    """Video for this episode, or clear a stale « downloaded » claim."""
    return local_video_in(db, storage_layout(db, settings), item, now)


def local_video_in(db: Session, layout: StorageLayout, item: MediaItem, now) -> Path | None:
    media = db.get(Media, item.media_id)
    if media is None:
        return None
    skip = library_skip(layout)
    direct = _video_path(item.file_path, skip)
    if direct is not None:
        _mark_downloaded(db, media, item, direct, now)
        return direct
    indexed = _indexed_video(db, item, skip, now)
    if indexed is not None:
        _mark_downloaded(db, media, item, indexed, now)
        return indexed
    found = find_episode_on_disk(
        movies_dir=layout.movies_dir,
        series_dir=layout.series_dir,
        media_type=media.type,
        title=media.title,
        year=media.year,
        season=item.season,
        episode=item.episode,
        tmdb_id=media.tmdb_id,
        skip=skip,
    )
    if found is not None:
        _mark_downloaded(db, media, item, found, now)
        from mediatracker.library.scanner import remember_filed

        remember_filed(db, found, media, item, now)
        return found
    if absence_updates_allowed(db):
        forget_local_file(db, media, item, now, skip=skip)
    return None


def _video_path(value: str | None, skip: tuple[Path, ...]) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if skip and is_under(path, skip):
        return None
    if valid_video(path):
        return path
    return None


def _indexed_video(db: Session, item: MediaItem, skip: tuple[Path, ...], now) -> Path | None:
    if item.id is None:
        return None
    rows = list(
        db.scalars(
            select(LibraryFile).where(
                LibraryFile.media_item_id == item.id,
                LibraryFile.status == "matched",
                LibraryFile.missing_at.is_(None),
            )
        ).all()
    )
    kept: Path | None = None
    for row in rows:
        path = _video_path(row.path, skip)
        if path is None:
            if absence_updates_allowed(db):
                row.status = "missing"
                row.missing_at = now
            continue
        if kept is None:
            kept = path
    return kept


def _mark_downloaded(db: Session, media: Media, item: MediaItem, path: Path, now) -> None:
    reappear = not item.downloaded or not item.file_path or is_file_absent(item)
    item.file_path = str(path)
    item.downloaded = True
    item.downloaded_at = now if reappear else (item.downloaded_at or now)
    clear_forget_traces(item)
    if item.state in _CLAIMED:
        item.state = "downloaded"
    if reappear:
        if media.type == "film":
            from mediatracker.core.film_follow import stop_following_downloaded_film

            stop_following_downloaded_film(db, media, reason="downloaded")
        elif media.type == "series":
            from mediatracker.core.series_follow import stop_following_complete_series

            stop_following_complete_series(db, media, today=now.date())
