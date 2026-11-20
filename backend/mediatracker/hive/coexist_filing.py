"""Rangement côte à côte pour une version Hive demandée explicitement."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.library_video import valid_video
from mediatracker.core.naming import atomic_move, library_alternate_destination
from mediatracker.core.pipeline.constants import VIDEO_EXT
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.hive.download_pipeline import hive_fetch_ctx, is_hive_fetch_download


def explicit_hive_requested_version_key(db: Session, row: Download, settings: Settings) -> str | None:
    if not is_hive_fetch_download(db, settings, row):
        return None
    ctx = hive_fetch_ctx(db, row)
    if ctx is None:
        return None
    raw = (ctx.requested_version_key or "").strip()
    return raw or None


def file_alongside_incumbent(
    db: Session,
    row: Download,
    item: MediaItem,
    media: Media,
    source: Path,
    occupant: Path,
    now,
    settings: Settings,
    *,
    version_key: str,
) -> bool:
    """Range le téléchargement à côté de l’occupant. Retourne False si le déplacement a échoué."""
    if not occupant.is_file() or not valid_video(occupant):
        return False
    if source.suffix.lower() not in VIDEO_EXT:
        return False
    alt = library_alternate_destination(occupant, version_key)
    if alt.resolve() == occupant.resolve():
        return False
    suffix_label = alt.stem.removeprefix(f"{occupant.stem} - ")
    n = 2
    while alt.is_file():
        alt = occupant.with_name(f"{occupant.stem} - {suffix_label} ({n}){occupant.suffix}")
        n += 1
    try:
        atomic_move(source, alt)
    except OSError:
        return False
    from mediatracker.core.pipeline.queue import _finalize_download_row, _record_hive_received_fingerprint  # noqa: PLC0415

    prior = item.file_path
    if prior and Path(prior).is_file():
        item.file_path = prior
    else:
        item.file_path = str(occupant.resolve())
    item.downloaded = True
    item.downloaded_at = item.downloaded_at or now
    item.state = "downloaded"
    _finalize_download_row(
        db,
        row,
        item,
        media,
        alt,
        now,
        settings,
        store_release=False,
        library_path_for_import=str(occupant.resolve()),
    )
    _record_hive_received_fingerprint(db, settings, row, item, alt, now)
    return True
