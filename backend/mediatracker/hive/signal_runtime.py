"""Effets de bord après changement de réglages signaux Hive (auto-acceptation, etc.)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.hive.signal_policy import (
    HIVE_AUTO_ACCEPT_REQUESTS_KEY,
    HIVE_AUTO_TAKE_WANTED_KEY,
    hive_auto_accept_requests_enabled,
    hive_auto_take_wanted_enabled,
)
from mediatracker.hive.signal_requests import try_auto_accept_incoming_for_instance
from mediatracker.hive.signal_wanted import maybe_try_auto_take_wanted


def after_hive_signal_settings_patch(db: Session, settings: Settings, changed: set[str]) -> None:
    moment = datetime.now(timezone.utc)
    if HIVE_AUTO_ACCEPT_REQUESTS_KEY in changed and hive_auto_accept_requests_enabled(db):
        try_auto_accept_incoming_for_instance(db, settings, now=moment)
    if HIVE_AUTO_TAKE_WANTED_KEY in changed and hive_auto_take_wanted_enabled(db):
        maybe_try_auto_take_wanted(db, settings, now=moment)
