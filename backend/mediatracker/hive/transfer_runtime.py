"""Application des réglages Hive transfer (débit envoi pair, limites aria2 réception)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.hive.transfer_settings import transfer_send_bps

HIVE_TRANSFER_RECV_KEY = "hive_transfer_recv_bps"
HIVE_TRANSFER_SEND_KEY = "hive_transfer_send_bps"


def apply_hive_send_bandwidth(app: Any, db: Session) -> None:
    limiter = getattr(app.state, "hive_send_bandwidth", None)
    if not isinstance(limiter, TransferBandwidthLimiter):
        return
    limiter.set_bps(float(transfer_send_bps(db)))


def sync_hive_recv_limits(db: Session, settings: Settings) -> int:
    """Pousse max-download-limit sur les téléchargements Hive actifs dans aria2."""
    from mediatracker.core.pipeline.clients import _downloader
    from mediatracker.hive.download_pipeline import sync_hive_recv_rate_limits

    _row, downloader = _downloader(db, settings, None)
    if downloader is None:
        return 0
    return sync_hive_recv_rate_limits(db, downloader)


def after_hive_transfer_settings_patch(
    request: Request,
    db: Session,
    changed: set[str],
) -> None:
    settings: Settings = request.app.state.settings
    if HIVE_TRANSFER_SEND_KEY in changed:
        apply_hive_send_bandwidth(request.app, db)
    if HIVE_TRANSFER_RECV_KEY in changed:
        sync_hive_recv_limits(db, settings)
