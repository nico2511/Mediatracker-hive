"""Rangement bibliothèque après réception Hive (hors ancien moteur HiveTransferJob)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.naming import atomic_move, filing_folder, library_destination
from mediatracker.core.pipeline.constants import VIDEO_EXT
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import Download, HiveFileFingerprint, Media, MediaItem
from mediatracker.library.identity import release_conflicts

logger = logging.getLogger(__name__)


def _video_ext(part: Path, fiche: dict) -> str:
    for raw in (fiche.get("ext"), fiche.get("extension")):
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            return text if text.startswith(".") else f".{text}"
    suffix = part.suffix.lower()
    if suffix in VIDEO_EXT:
        return suffix
    return ".mkv"


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def file_verified_part(
    db: Session,
    settings: Settings,
    *,
    part: Path,
    media: Media,
    item: MediaItem,
    fiche: dict,
    now: datetime,
    download: Download | None = None,
) -> tuple[Path | None, str | None]:
    """Déplace le fichier vérifié. Retourne (dest, code_erreur)."""
    if not part.is_file():
        return None, "missing_part"
    if part.stat().st_size <= 0:
        return None, "empty_file"
    ext = _video_ext(part, fiche)
    if ext not in VIDEO_EXT:
        return None, "unsupported_ext"
    if media.type == "series" and release_conflicts(part.name, media):
        return None, "wrong_series"
    from mediatracker.core.video_integrity import integrity_issue

    issue = integrity_issue(part)
    if issue is not None:
        return None, "suspect"

    layout = storage_layout(db, settings)
    library_root = layout.series_dir if media.type == "series" else layout.movies_dir
    dest = library_destination(
        movies_dir=layout.movies_dir,
        series_dir=layout.series_dir,
        media_type=media.type,
        title=media.title,
        year=media.year,
        season=item.season,
        episode=item.episode,
        ext=ext,
        tmdb_id=media.tmdb_id,
        library_folder=filing_folder(library_root, title=media.title, year=media.year, tmdb_id=media.tmdb_id),
    )
    from mediatracker.core.library_video import find_library_video, valid_video

    if dest.exists() and not valid_video(dest):
        return None, "file_exists"
    occupant = (
        dest
        if valid_video(dest)
        else find_library_video(
            movies_dir=layout.movies_dir,
            series_dir=layout.series_dir,
            media_type=media.type,
            title=media.title,
            year=media.year,
            season=item.season,
            episode=item.episode,
            tmdb_id=media.tmdb_id,
        )
    )
    from mediatracker.core.profile_replacement import (
        file_incoming_for_replacement,
        replacing_proposal_for_download,
        replacing_proposal_for_item,
    )

    proposal = replacing_proposal_for_download(db, download) if download is not None else None
    if proposal is None and item.id is not None:
        proposal = replacing_proposal_for_item(db, item.id)
    force_incoming = proposal is not None

    if occupant is not None and occupant.is_file():
        if not force_incoming and item.file_path and Path(item.file_path).resolve() == occupant.resolve():
            return occupant, None
        if _same_path(part, occupant) and not force_incoming:
            return occupant, None
        if force_incoming:
            kept = file_incoming_for_replacement(db, settings, media, item, part, dest, occupant, now)
            return (kept, None) if kept is not None else (None, "move_failed")
        from mediatracker.core.library_video import better_video

        keep_incumbent = better_video(
            part,
            occupant,
            challenger_names=(part.name,),
            challenger_qualities=(None,),
            incumbent_names=(occupant.name,),
            incumbent_qualities=(item.release_quality,),
        )
        if keep_incumbent:
            from mediatracker.core.pipeline.queue import _trash_video  # noqa: PLC2701

            _trash_video(db, settings, part, now, media, item)
            return occupant, None
        from mediatracker.core.pipeline.queue import _trash_video  # noqa: PLC2701

        _trash_video(db, settings, occupant, now, media, item)
    try:
        atomic_move(part, dest)
    except OSError as exc:
        code = str(exc)
        if code == "CROSS_DEVICE":
            return None, "cross_device"
        logger.info("Rangement Hive impossible (%s).", exc)
        return None, "move_failed"
    return dest, None


def finalize_received_hive_file(
    db: Session,
    settings: Settings,
    *,
    part: Path,
    media: Media,
    item: MediaItem,
    fiche: dict,
    cle: dict,
    sha256_hex: str,
    now: datetime | None = None,
    download: Download | None = None,
) -> str | None:
    """Range le fichier reçu et synchronise empreinte / partage. Retourne un code d’erreur ou None."""
    moment = now or datetime.now(timezone.utc)
    dest, err = file_verified_part(
        db,
        settings,
        part=part,
        media=media,
        item=item,
        fiche=fiche,
        now=moment,
        download=download,
    )
    if err is not None or dest is None:
        if download is not None:
            from mediatracker.core.profile_replacement import on_download_terminal

            on_download_terminal(
                db,
                settings,
                media,
                item,
                download,
                success=False,
                new_dest=None,
                now=moment,
            )
        return err or "filing_failed"

    item.file_path = str(dest.resolve())
    item.state = "downloaded"
    item.downloaded = True
    from mediatracker.library.scanner import remember_filed

    remember_filed(db, dest, media, item, moment)
    if item.id is not None and sha256_hex:
        try:
            size = dest.stat().st_size
            mtime_ns = getattr(dest.stat(), "st_mtime_ns", int(dest.stat().st_mtime * 1_000_000_000))
        except OSError:
            size = int(part.stat().st_size)
            mtime_ns = moment.timestamp() * 1_000_000_000
        row = db.get(HiveFileFingerprint, item.id)
        if row is None:
            db.add(
                HiveFileFingerprint(
                    media_item_id=item.id,
                    path=str(dest.resolve()),
                    size=size,
                    mtime_ns=int(mtime_ns),
                    sha256_hex=str(sha256_hex).lower(),
                    partial_size=size,
                    updated_at=moment,
                )
            )
        else:
            row.path = str(dest.resolve())
            row.size = size
            row.mtime_ns = int(mtime_ns)
            row.sha256_hex = str(sha256_hex).lower()
            row.partial_size = size
            row.updated_at = moment
        from mediatracker.hive.catalog import sync_hive_catalog_item
        from mediatracker.hive.share_policy import grant_share_intent, hive_catalog_auto_share_enabled

        db.flush()
        if hive_catalog_auto_share_enabled(db):
            grant_share_intent(db, item.id)
        sync_hive_catalog_item(db, settings, item.id, now=moment)
        from mediatracker.hive.signal_requests import maybe_try_auto_accept_stored_incoming

        maybe_try_auto_accept_stored_incoming(db, settings, now=moment)
    if download is not None:
        from mediatracker.core.profile_replacement import on_download_terminal, record_served_profile_on_finish

        record_served_profile_on_finish(db, item, download)
        on_download_terminal(db, settings, media, item, download, success=True, new_dest=dest, now=moment)
    return None
