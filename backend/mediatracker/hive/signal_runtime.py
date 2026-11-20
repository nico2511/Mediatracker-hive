"""Effets de bord après changement de réglages signaux Hive (auto-acceptation, etc.)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.hive.signal_policy import HIVE_AUTO_ACCEPT_REQUESTS_KEY, hive_auto_accept_requests_enabled
from mediatracker.hive.signal_requests import try_auto_accept_incoming_for_instance


def after_hive_signal_settings_patch(db: Session, settings: Settings, changed: set[str]) -> None:
    if HIVE_AUTO_ACCEPT_REQUESTS_KEY not in changed:
        return
    if not hive_auto_accept_requests_enabled(db):
        return
    try_auto_accept_incoming_for_instance(db, settings, now=datetime.now(timezone.utc))
