"""Prise de main catalogue Hive (téléchargement classique / autre transfert Hive)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Download, DownloadHiveFetch, Media, MediaItem
from mediatracker.hive.source_catalog import HiveCatalogMatch

_CLASSIC_BUSY = ("pending", "resolving", "queued", "downloading", "post_processing")


def preempt_for_explicit_catalogue_request(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    match: HiveCatalogMatch,
    now: datetime,
) -> str | None:
    """Annule les téléchargements en cours sur le slot (y compris Hive aria2)."""
    moment = now
    if item.id is not None:
        rows = list(
            db.scalars(
                select(Download).where(
                    Download.media_item_id == item.id,
                    Download.state.in_(_CLASSIC_BUSY),
                )
            ).all()
        )
        if rows:
            blocking = [row for row in rows if row.state == "post_processing"]
            if blocking:
                return (
                    "Un téléchargement AllDebrid est en cours de finalisation ; "
                    "réessayez dans un instant ou annulez depuis la file."
                )
            from mediatracker.core.pipeline import _downloader, cancel_job

            _provider, downloader = _downloader(db, settings, None)
            del _provider
            for row in rows:
                cancel_job(db, row, downloader, now=moment, settings=settings)
                row.error_code = "HIVE_REQUEST"
                row.error = "Interrompu pour une demande depuis le catalogue Hive."

    target_sha = str(match.sha256_hex).lower()
    if item.id is not None:
        hive_rows = list(
            db.scalars(
                select(Download)
                .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
                .where(
                    Download.media_item_id == item.id,
                    Download.state.in_(_CLASSIC_BUSY),
                    DownloadHiveFetch.sha256_hex != target_sha,
                )
            ).all()
        )
        if hive_rows:
            from mediatracker.core.pipeline import _downloader, cancel_job

            _provider, downloader = _downloader(db, settings, None)
            del _provider
            for row in hive_rows:
                cancel_job(db, row, downloader, now=moment, settings=settings)
                row.error_code = "HIVE_REQUEST"
                row.error = "Remplacé par une autre version demandée dans le catalogue Hive."
    return None
