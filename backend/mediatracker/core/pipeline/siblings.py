"""When one job for an episode finishes, retire the errors left by the others."""

from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, aliased

from mediatracker.config import Settings
from mediatracker.core.pipeline.constants import _STALE_WHEN_OBTAINED, logger
from mediatracker.db.models import Download, MediaItem


def _mark_obtained(row: Download, now: datetime) -> None:
    """The file is already in the library. This attempt is no longer an error."""
    row.state = "done"
    row.error_code = None
    row.error = None
    row.progress = 100
    row.handle = None
    row.speed = None
    row.eta_seconds = None
    row.dismissed = True
    if row.finished_at is None:
        row.finished_at = now


def _sibling_already_obtained(db: Session, row: Download) -> bool:
    item = db.get(MediaItem, row.media_item_id)
    if item is None or not item.downloaded:
        return False
    other = db.scalar(
        select(Download.id).where(
            Download.media_item_id == row.media_item_id,
            Download.id != row.id,
            Download.state == "done",
        )
    )
    return other is not None


def _heal_obtained_item(db: Session, media_item_id: int) -> None:
    item = db.get(MediaItem, media_item_id)
    if item is not None and item.downloaded and item.state in {"failed", "error"}:
        item.state = "downloaded"


def _settle_obtained_siblings(db: Session, now: datetime, settings: Settings) -> None:
    """When one job for an episode finishes, drop the errors left by the others.

    Idle ticks only ask whether any downloaded item still has a stale sibling
    or a dirty done row. They do not load every finished job.
    """
    winner = aliased(Download)
    stale_ids = set(
        db.scalars(
            select(Download.media_item_id)
            .join(MediaItem, MediaItem.id == Download.media_item_id)
            .where(
                MediaItem.downloaded.is_(True),
                Download.state.in_(_STALE_WHEN_OBTAINED),
                exists(
                    select(winner.id).where(
                        winner.media_item_id == MediaItem.id,
                        winner.state == "done",
                    )
                ),
            )
        ).all()
    )
    dirty_ids = set(
        db.scalars(
            select(Download.media_item_id)
            .join(MediaItem, MediaItem.id == Download.media_item_id)
            .where(
                MediaItem.downloaded.is_(True),
                Download.state == "done",
                or_(
                    Download.error_code.is_not(None),
                    and_(Download.error.is_not(None), Download.error.notlike("/%")),
                ),
            )
        ).all()
    )
    item_ids = stale_ids | dirty_ids
    if not item_ids:
        return
    rows = list(db.scalars(select(Download).where(Download.media_item_id.in_(item_ids))).all())
    by_item: dict[int, list[Download]] = {}
    for row in rows:
        by_item.setdefault(row.media_item_id, []).append(row)
    from mediatracker.core.presence import local_video_in
    from mediatracker.core.storage import storage_layout

    layout = storage_layout(db, settings)
    for item_id, jobs in by_item.items():
        item = db.get(MediaItem, item_id)
        if item is None or not item.downloaded:
            continue
        if local_video_in(db, layout, item, now) is None:
            continue
        finished = [row for row in jobs if row.state == "done"]
        if not finished:
            continue
        for done in finished:
            if done.error_code is not None:
                done.error_code = None
            if done.error and not done.error.startswith("/"):
                done.error = None
        _heal_obtained_item(db, item_id)
        slot = "film"
        if item.season is not None and item.episode is not None:
            slot = f"S{item.season:02d}E{item.episode:02d}"
        for sibling in jobs:
            if sibling.forced or sibling.state not in _STALE_WHEN_OBTAINED:
                continue
            logger.info(
                "Erreur devenue obsolète : un autre téléchargement a abouti. job=%s episode=%s",
                sibling.id,
                slot,
            )
            _mark_obtained(sibling, now)

