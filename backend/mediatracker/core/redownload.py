"""Queue a new download when the episode file is absent from disk.

Uses stored hoster links when they are still alive. Otherwise schedules a
fresh Wawacity pass (REDISCOVER), subject to the per-fiche remote search cooldown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.file_status import FileStatusIndex
from mediatracker.core.force_download import _revive_links
from mediatracker.core.forget import is_voluntarily_deleted
from mediatracker.core.pipeline.constants import REDISCOVER_RESERVED
from mediatracker.core.pipeline.download_guard import active_download_for_item
from mediatracker.core.pipeline.jobs import _choose_link
from mediatracker.core.precheck import DUPLICATE_IGNORED, NO_LINK_RETRY_LATER, REDOWNLOAD_STARTED
from mediatracker.core.remote_search_limit import begin_remote_search
from mediatracker.core.selection import parse_hoster_preference
from mediatracker.core.source_poll import refresh_library_claims
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.events import record_event

RedownloadStatus = Literal["started", "duplicate_ignored", "no_link", "busy", "not_absent"]

_REDISCOVER_MESSAGE = "Nouvelle recherche Wawacity planifiée."


@dataclass(frozen=True)
class RedownloadResult:
    status: RedownloadStatus
    message: str
    download: Download | None = None


def redownload_item(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    *,
    now: datetime,
) -> RedownloadResult:
    refresh_library_claims(db, media, [item], settings, now)
    index = FileStatusIndex.load(db, [item])
    file_status = index.for_item(item)
    if file_status == "downloading" or _in_progress(db, item.id):
        return RedownloadResult("busy", "Un téléchargement est déjà en cours pour cet épisode.")
    if is_voluntarily_deleted(item) or file_status == "trash":
        return RedownloadResult("not_absent", "Ce fichier a été retiré volontairement.")
    if file_status != "absent":
        return RedownloadResult("not_absent", "Le fichier est encore sur le disque ou en corbeille.")

    from mediatracker.core.presence import local_video

    if local_video(db, settings, item, now) is not None:
        _emit(db, media, item, None, "duplicate_ignored", DUPLICATE_IGNORED)
        return RedownloadResult("duplicate_ignored", DUPLICATE_IGNORED)

    from mediatracker.core.pipeline.jobs import _text_setting

    preference = parse_hoster_preference(_text_setting(db, "hoster_preference", settings.hoster_preference or ""))
    probe = Download(media_item_id=item.id, state="pending", forced=True)
    has_live = _choose_link(db, probe, item, preference, media=media, settings=settings) is not None

    if has_live:
        row = Download(media_item_id=item.id, state="pending", forced=True, created_at=now)
        db.add(row)
        db.flush()
        _revive_links(db, item.id, now=now)
        _emit(db, media, item, row, "started", REDOWNLOAD_STARTED)
        return RedownloadResult("started", REDOWNLOAD_STARTED, row)

    if not begin_remote_search(media, now, db, settings):
        _emit(db, media, item, None, "no_link", NO_LINK_RETRY_LATER)
        return RedownloadResult("no_link", NO_LINK_RETRY_LATER)

    row = Download(
        media_item_id=item.id,
        state="pending",
        forced=True,
        created_at=now,
        error_code=REDISCOVER_RESERVED,
        error=_REDISCOVER_MESSAGE,
    )
    db.add(row)
    db.flush()
    _emit(db, media, item, row, "started", REDOWNLOAD_STARTED)
    return RedownloadResult("started", REDOWNLOAD_STARTED, row)


def _in_progress(db: Session, item_id: int | None) -> bool:
    if item_id is None:
        return False
    return active_download_for_item(db, item_id) is not None


def _emit(
    db: Session,
    media: Media,
    item: MediaItem,
    row: Download | None,
    status: str,
    message: str,
) -> None:
    record_event(
        db,
        "download.redownload",
        media_id=media.id,
        item_id=item.id,
        payload={
            "title": media.title,
            "media_id": media.id,
            "item_id": item.id,
            "download_id": row.id if row is not None else None,
            "season": item.season,
            "episode": item.episode,
            "status": status,
            "message": message,
            "level": "info",
        },
    )
