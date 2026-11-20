"""Which download sources may be used right now (AllDebrid + Hive gates).

User settings are not changed: sources stay enabled in storage but are skipped
when AllDebrid is not connected, Hive is off, or « Ruche seule » is on.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.hive.identity import hive_enabled_in_db, hive_only_in_db
from mediatracker.integrations.alldebrid_account import is_connected

logger = logging.getLogger(__name__)

ALLDEBRID_GATED_SOURCES = frozenset(
    {"wawacity", "free_telecharger", "comet", "torrentio", "stremio_addons"}
)
DOWNLOAD_BLOCKED_MESSAGE = "Renseigne ta clé AllDebrid ou active Hive pour télécharger"
DOWNLOAD_BLOCKED_CODE = "DOWNLOAD_SOURCES_UNAVAILABLE"
_ALLDEBRID_UNCONFIGURED = "AllDebrid non configuré (clé API ou liaison PIN)."
_HIVE_ONLY_REMOTE = "Ruche seule : seules les sources Hive sont utilisées pour le téléchargement automatique."


def source_usable(db: Session, settings: Settings, name: str, *, for_manual_probe: bool = False) -> bool:
    key = (name or "").strip().lower()
    if not key:
        return False
    if key == "hive":
        return hive_enabled_in_db(db)
    if not for_manual_probe and hive_only_in_db(db) and hive_enabled_in_db(db):
        return False
    return is_connected(settings, db)


def global_enabled_download_sources(db: Session, settings: Settings) -> list[str]:
    """Sources activées dans Réglages (même critère que l’UI et le probe)."""
    from mediatracker.core.pipeline.jobs import _text_setting
    from mediatracker.core.selection import (
        DEFAULT_DOWNLOAD_SOURCE_ORDER,
        parse_download_source_order,
        registered_download_sources,
    )

    default_raw = settings.download_source_order or DEFAULT_DOWNLOAD_SOURCE_ORDER
    raw = _text_setting(db, "download_source_order", default_raw)
    try:
        return parse_download_source_order(raw)
    except ValueError:
        known = set(registered_download_sources())
        kept: list[str] = []
        ignored: list[str] = []
        for part in raw.split(","):
            name = part.strip().lower()
            if not name:
                continue
            if name in known:
                if name not in kept:
                    kept.append(name)
            else:
                ignored.append(name)
        if ignored:
            logger.warning(
                "download_source_order contient des jetons ignorés : %s",
                ", ".join(ignored),
            )
        return kept


def filter_enabled_download_source_order(db: Session, settings: Settings, order: Sequence[str]) -> list[str]:
    enabled = global_enabled_download_sources(db, settings)
    enabled_set = set(enabled)
    kept = [name for name in order if name in enabled_set]
    if kept:
        return kept
    return list(enabled)


def download_source_enabled_in_settings(db: Session, settings: Settings, name: str) -> bool:
    key = (name or "").strip().lower()
    if not key:
        return False
    return key in set(global_enabled_download_sources(db, settings))


def filter_usable_source_order(db: Session, settings: Settings, order: Sequence[str]) -> list[str]:
    return [name for name in order if source_usable(db, settings, name)]


def available_download_sources(db: Session, settings: Settings) -> list[str]:
    from mediatracker.core.source_state import DOWNLOAD_SOURCE_TYPES

    return [name for name in DOWNLOAD_SOURCE_TYPES if source_usable(db, settings, name)]


def any_download_source_available(db: Session, settings: Settings) -> bool:
    return bool(available_download_sources(db, settings))


def source_unusable_reason(
    db: Session, settings: Settings, name: str, *, for_manual_probe: bool = False
) -> str | None:
    if source_usable(db, settings, name, for_manual_probe=for_manual_probe):
        return None
    key = (name or "").strip().lower()
    if key == "hive":
        return "Hive désactivé."
    if not for_manual_probe and hive_only_in_db(db) and hive_enabled_in_db(db) and key != "hive":
        return _HIVE_ONLY_REMOTE
    if key in ALLDEBRID_GATED_SOURCES or key not in {"hive", ""}:
        return _ALLDEBRID_UNCONFIGURED
    return "Source indisponible."
