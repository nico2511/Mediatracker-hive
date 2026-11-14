"""User pause of the download queue.

Distinct from an AllDebrid AUTH_BLOCKED pause and from the disk guard.
The flag lives in the settings table. The worker refuses to start new jobs
while it is set, and pauses aria2 handles that already have one.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Download, Setting

logger = logging.getLogger(__name__)

HOLD_KEY = "system.downloads_paused"
HOLD_CODE = "USER_PAUSED"
HOLD_MESSAGE = "Téléchargements en pause."
_HOLDS = ("pending", "resolving", "queued", "downloading")


def downloads_held(db: Session) -> bool:
    row = db.get(Setting, HOLD_KEY)
    return row is not None and row.value == "1"


def set_downloads_held(db: Session, held: bool, *, downloader=None) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(Setting, HOLD_KEY)
    value = "1" if held else "0"
    if row is None:
        db.add(Setting(key=HOLD_KEY, value=value, is_secret=False, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    if held:
        hold_downloads(db, downloader)
    else:
        release_downloads(db, downloader)
    db.commit()


def hold_downloads(db: Session, downloader) -> None:
    rows = db.scalars(select(Download).where(Download.state.in_(_HOLDS))).all()
    for row in rows:
        if row.error_code == HOLD_CODE:
            continue
        if row.handle and downloader is not None and hasattr(downloader, "pause"):
            try:
                downloader.pause(row.handle)
            except Exception:
                logger.info("Pause aria2 ignorée pour le téléchargement %s.", row.id)
        if row.error_code not in (None, ""):
            continue
        row.error_code = HOLD_CODE
        row.error = HOLD_MESSAGE


def release_downloads(db: Session, downloader) -> None:
    rows = db.scalars(select(Download).where(Download.error_code == HOLD_CODE)).all()
    for row in rows:
        if row.handle and downloader is not None and hasattr(downloader, "resume"):
            try:
                downloader.resume(row.handle)
            except Exception:
                logger.info("Reprise aria2 ignorée pour le téléchargement %s.", row.id)
        if row.state in _HOLDS:
            row.error_code = None
            row.error = None
