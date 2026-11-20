"""Pause downloads when the media volume runs low, and say so once per episode."""

import logging
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.storage import incomplete_dir, storage_layout
from mediatracker.db.models import Download, Setting
from mediatracker.events import record_event
from mediatracker.settings_int import int_setting as _int_setting

logger = logging.getLogger(__name__)

DISK_LOW_KEY = "system.disk_low"
GIB = 1024 * 1024 * 1024
LAUNCH_SIZE_MARGIN = 50 * 1024 * 1024
LAUNCH_UNKNOWN_SIZE_FALLBACK = 512 * 1024 * 1024


def threshold_bytes(db: Session, settings: Settings) -> int:
    gb = _int_setting(db, "disk_min_free_gb", settings.disk_min_free_gb)
    if gb <= 0:
        return 0
    return gb * GIB


def launch_free_required(db: Session, settings: Settings, size: int | None) -> int:
    """Bytes that must be free on the download volume before aria2 starts a job."""
    reserve = threshold_bytes(db, settings)
    if size:
        needed = size + LAUNCH_SIZE_MARGIN
        if reserve > 0:
            needed += reserve
        return needed
    if reserve > 0:
        return reserve
    return LAUNCH_UNKNOWN_SIZE_FALLBACK


def free_bytes(path: Path, disk_free: Callable[[Path], int] | None) -> int | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        if disk_free is not None:
            return int(disk_free(path))
        return shutil.disk_usage(path).free
    except OSError:
        logger.info("Espace libre illisible pour %s.", path)
        return None


def enforce_disk_guard(
    db: Session,
    settings: Settings,
    downloader,
    now: datetime,
    disk_free: Callable[[Path], int] | None,
) -> bool:
    """Return True when downloads must stay paused. Notifies on the falling edge."""
    del now
    target = incomplete_dir(storage_layout(db, settings))
    limit = threshold_bytes(db, settings)
    if limit <= 0:
        _mark(db, False)
        return False
    free = free_bytes(target, disk_free)
    if free is None:
        return False
    low = free < limit
    if low:
        _pause_active(db, downloader)
        if not _flag(db):
            record_event(
                db,
                "disk.low",
                level="warning",
                payload={
                    "title": "Espace disque",
                    "free_bytes": free,
                    "threshold_bytes": limit,
                    "level": "warning",
                },
            )
            _mark(db, True)
            logger.info("Espace libre sous le seuil (%s o / %s o). Téléchargements en pause.", free, limit)
        return True
    if _flag(db):
        _resume_paused(db, downloader)
        _mark(db, False)
        logger.info("Espace disque revenu au-dessus du seuil. Téléchargements repris.")
    return False


def _pause_active(db: Session, downloader) -> None:
    rows = db.scalars(
        select(Download).where(Download.state.in_(("pending", "queued", "downloading")))
    ).all()
    for row in rows:
        if row.error_code == "DISK_LOW":
            continue
        if row.handle and downloader is not None and hasattr(downloader, "pause"):
            try:
                downloader.pause(row.handle)
            except Exception:
                logger.info("Pause aria2 ignorée pour le téléchargement %s.", row.id)
        if row.error_code not in (None, "", "DISK_LOW"):
            continue
        row.error_code = "DISK_LOW"
        row.error = "Espace disque insuffisant : téléchargement en pause."


def _resume_paused(db: Session, downloader) -> None:
    rows = db.scalars(select(Download).where(Download.error_code == "DISK_LOW")).all()
    for row in rows:
        if row.handle and downloader is not None and hasattr(downloader, "resume"):
            try:
                downloader.resume(row.handle)
            except Exception:
                logger.info("Reprise aria2 ignorée pour le téléchargement %s.", row.id)
        if row.state in {"pending", "queued", "downloading"}:
            row.error_code = None
            row.error = None


def _flag(db: Session) -> bool:
    row = db.get(Setting, DISK_LOW_KEY)
    return row is not None and row.value == "1"


def _mark(db: Session, low: bool) -> None:
    row = db.get(Setting, DISK_LOW_KEY)
    value = "1" if low else "0"
    if row is None:
        if not low:
            return
        db.add(Setting(key=DISK_LOW_KEY, value=value, is_secret=False, updated_at=datetime_now()))
        return
    row.value = value
    row.updated_at = datetime_now()


def datetime_now():
    from datetime import timezone

    return datetime.now(timezone.utc)


