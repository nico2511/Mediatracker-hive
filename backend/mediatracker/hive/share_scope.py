"""Portée fiche / saison pour le partage Hive (tous les fichiers locaux du périmètre)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, MediaItem
from mediatracker.hive.catalog import item_is_shareable_base, sync_hive_catalog_item
from mediatracker.hive.share_exclusions import replace_share_exclusions
from mediatracker.hive.share_policy import effective_exclude_all_for_ui, grant_share_intent, revoke_share_intent


def item_has_local_file(item: MediaItem) -> bool:
    if not item.downloaded:
        return False
    path = (item.file_path or "").strip()
    if not path:
        return False
    return Path(path).is_file()


def scope_media_items(db: Session, media_id: int, *, season: int | None = None) -> list[MediaItem]:
    q = select(MediaItem).where(MediaItem.media_id == media_id).order_by(
        MediaItem.season.asc().nullsfirst(),
        MediaItem.episode.asc().nullsfirst(),
        MediaItem.id.asc(),
    )
    if season is not None:
        q = q.where(MediaItem.season == season)
    rows = list(db.scalars(q).all())
    return [row for row in rows if item_has_local_file(row)]


@dataclass(frozen=True)
class SharingScopeSummary:
    media_id: int
    season: int | None
    file_count: int
    shared_count: int
    any_shared: bool
    any_shareable: bool
    pull_only: bool


def sharing_scope_summary(
    db: Session,
    media_id: int,
    *,
    settings: Settings,
    season: int | None = None,
) -> SharingScopeSummary:
    from mediatracker.hive.catalog import _public_address_configured
    from mediatracker.hive.identity import hive_enabled_in_db

    items = scope_media_items(db, media_id, season=season)
    shared = 0
    shareable = 0
    for item in items:
        if item_is_shareable_base(db, item, hive_enabled=True):
            shareable += 1
        if item_is_shareable_base(db, item, hive_enabled=True) and not effective_exclude_all_for_ui(
            db, int(item.id)
        ):
            shared += 1
    pull_only = hive_enabled_in_db(db) and not _public_address_configured(db)
    return SharingScopeSummary(
        media_id=media_id,
        season=season,
        file_count=len(items),
        shared_count=shared,
        any_shared=shared > 0,
        any_shareable=shareable > 0 or len(items) > 0,
        pull_only=pull_only,
    )


def apply_sharing_to_scope(
    db: Session,
    settings: Settings,
    media_id: int,
    *,
    season: int | None,
    exclude_all: bool,
    ruche_ids: list[bytes],
    moment,
) -> list[int]:
    """Applique intent + exclusions + sync catalogue sur chaque fichier local du périmètre."""
    touched: list[int] = []
    for item in scope_media_items(db, media_id, season=season):
        assert item.id is not None
        item_id = int(item.id)
        replace_share_exclusions(db, item_id, exclude_all=exclude_all, ruche_ids=ruche_ids)
        if exclude_all:
            revoke_share_intent(db, item_id)
        else:
            grant_share_intent(db, item_id)
        sync_hive_catalog_item(db, settings, item_id, now=moment)
        touched.append(item_id)
    if touched:
        from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store

        refresh_hive_catalogue_store(db, now=moment, settings=settings)
        from mediatracker.core.worker_wake import REASON_API_HIVE, wake_worker

        wake_worker(db, settings, REASON_API_HIVE, commit=False)
    return touched


def ensure_media_exists(db: Session, media_id: int) -> Media:
    media = db.get(Media, media_id)
    if media is None:
        from mediatracker.api.errors import Problem

        raise Problem(404, "Introuvable", "Fiche média introuvable.")
    return media
