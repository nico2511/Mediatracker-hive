"""Affichage catalogue / liens : masquer les flux des sources non utilisables (ex. sans AllDebrid)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Link, Provider

NOTICE_NO_LATEST_SOURCES = "Aucune source de nouveautés utilisable"
_MANUAL_SOURCE = "manual"


def visible_latest_source_types(db: Session, settings: Settings) -> list[str]:
    from mediatracker.core.source_availability import source_usable
    from mediatracker.providers.sources.loading import _with_capability

    types: list[str] = []
    for row, _entry in _with_capability(db, "latest"):
        if source_usable(db, settings, row.type):
            types.append(row.type)
    return types


def any_latest_catalog_usable(db: Session, settings: Settings) -> bool:
    return bool(visible_latest_source_types(db, settings))


def catalog_entry_visible(db: Session, settings: Settings, source_type: str) -> bool:
    key = (source_type or "").strip().lower()
    if not key:
        return False
    return key in visible_latest_source_types(db, settings)


def link_from_visible_source(db: Session, settings: Settings, link: Link) -> bool:
    provider = db.get(Provider, link.provider_id)
    if provider is None:
        return False
    if provider.kind != "source":
        return True
    if (provider.type or "").strip().lower() == _MANUAL_SOURCE:
        return True
    return catalog_entry_visible(db, settings, provider.type)
