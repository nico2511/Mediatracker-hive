"""Which download sources may be used right now (AllDebrid + Hive gates).

User settings are not changed: sources stay enabled in storage but are skipped
when AllDebrid is not connected or Hive is off.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.integrations.alldebrid_account import is_connected

ALLDEBRID_GATED_SOURCES = frozenset({"wawacity", "free_telecharger", "comet", "torrentio"})
DOWNLOAD_BLOCKED_MESSAGE = "Renseigne ta clé AllDebrid ou active Hive pour télécharger"
DOWNLOAD_BLOCKED_CODE = "DOWNLOAD_SOURCES_UNAVAILABLE"
_ALLDEBRID_UNCONFIGURED = "AllDebrid non configuré (clé API ou liaison PIN)."


def source_usable(db: Session, settings: Settings, name: str) -> bool:
    key = (name or "").strip().lower()
    if not key:
        return False
    if key == "hive":
        return hive_enabled_in_db(db)
    return is_connected(settings, db)


def filter_usable_source_order(db: Session, settings: Settings, order: Sequence[str]) -> list[str]:
    return [name for name in order if source_usable(db, settings, name)]


def available_download_sources(db: Session, settings: Settings) -> list[str]:
    from mediatracker.core.source_state import DOWNLOAD_SOURCE_TYPES

    return [name for name in DOWNLOAD_SOURCE_TYPES if source_usable(db, settings, name)]


def any_download_source_available(db: Session, settings: Settings) -> bool:
    return bool(available_download_sources(db, settings))


def source_unusable_reason(db: Session, settings: Settings, name: str) -> str | None:
    if source_usable(db, settings, name):
        return None
    key = (name or "").strip().lower()
    if key == "hive":
        return "Hive désactivé."
    if key in ALLDEBRID_GATED_SOURCES or key not in {"hive", ""}:
        return _ALLDEBRID_UNCONFIGURED
    return "Source indisponible."
