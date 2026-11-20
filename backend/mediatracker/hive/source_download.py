"""Mise en file transfert Hive depuis la file de téléchargement classique."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, MediaItem
from mediatracker.hive.download_aria2 import enqueue_hive_download_match, hive_reception_active_for_item
from mediatracker.hive.enqueue_types import HiveEnqueueResult
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.source_catalog import HiveCatalogMatch


@dataclass(frozen=True)
class HiveMatchResolution:
    outcome: str
    match: HiveCatalogMatch | None = None
    detail: str | None = None


def resolve_hive_match_for_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
    row=None,
    version_key: str | None = None,
) -> HiveMatchResolution:
    if db is None or not hive_enabled_in_db(db):
        return HiveMatchResolution("miss")
    if item.watched:
        return HiveMatchResolution("miss", detail="Cette version est déjà marquée comme vue.")
    from mediatracker.core.file_status import FileStatusIndex

    vk_early = version_key.strip() if isinstance(version_key, str) and version_key.strip() else None
    explicit_version = vk_early is not None
    file_status = FileStatusIndex.load(db, [item]).for_item(item)
    if not explicit_version and (file_status == "present" or (item.downloaded and item.file_path)):
        return HiveMatchResolution("miss", detail="Cette œuvre est déjà dans votre bibliothèque.")
    from mediatracker.core.profile_attempt_orchestration import (
        POLL_PROFILE_MISSING,
        load_profile_chain,
        pick_hive_catalog_for_item,
        resolve_download_poll_profile,
    )
    from mediatracker.core.profile_fallback import ProfileResolveError

    if row is not None:
        resolution = resolve_download_poll_profile(db, media, settings, row)
        if resolution.missing_explicit:
            row.error_code = POLL_PROFILE_MISSING
            row.error = "Profil du sondage introuvable."
            return HiveMatchResolution("profile_missing")
        attempt = resolution.profile
        if attempt is None:
            return HiveMatchResolution("miss")
    else:
        try:
            attempt = load_profile_chain(db, media, settings).principal
        except ProfileResolveError:
            return HiveMatchResolution("miss")
    try:
        chain = load_profile_chain(db, media, settings)
    except ProfileResolveError:
        chain = None
    if chain is None:
        from mediatracker.core.profile_attempt_orchestration import ProfileChain

        chain = ProfileChain((attempt,))
    vk = vk_early
    from mediatracker.hive.source_catalog import catalogue_request_miss_detail
    pick = pick_hive_catalog_for_item(
        db,
        media,
        item,
        chain,
        prefer_available=True,
        single_profile=attempt,
        version_key=vk,
        explicit_version=explicit_version,
    )
    if pick is None:
        detail = catalogue_request_miss_detail(
            db, media, item, profile=attempt, version_key=vk, explicit_version=explicit_version
        )
        return HiveMatchResolution("miss", detail=detail)
    match = pick.match
    if match is None:
        detail = catalogue_request_miss_detail(
            db, media, item, profile=attempt, version_key=vk, explicit_version=explicit_version
        )
        return HiveMatchResolution("miss", detail=detail)
    if explicit_version:
        from mediatracker.hive.catalogue_request_preempt import preempt_for_explicit_catalogue_request

        preempt_detail = preempt_for_explicit_catalogue_request(
            db, settings, media=media, item=item, match=match, now=now
        )
        if preempt_detail is not None:
            return HiveMatchResolution("miss", detail=preempt_detail)
    if hive_reception_active_for_item(db, item, match.sha256_hex, settings):
        return HiveMatchResolution("queued", match=match)
    if not match.row.disponible and not any(src.get("joignable") for src in match.sources):
        return HiveMatchResolution("pending", match=match)
    if match.expected_size <= 0:
        return HiveMatchResolution("pending", match=match)
    if row is not None:
        from mediatracker.hive.download_aria2 import attach_or_refresh_hive_fetch

        if (
            attach_or_refresh_hive_fetch(
                db, settings, download=row, match=match, now=now, requested_version_key=vk if explicit_version else None
            )
            is None
        ):
            return HiveMatchResolution("pending", match=match)
    elif (
        enqueue_hive_download_match(
            db,
            settings,
            media,
            item,
            match,
            now=now,
            requested_version_key=vk if explicit_version else None,
        )
        is None
    ):
        return HiveMatchResolution("pending", match=match)
    return HiveMatchResolution("queued", match=match)


def try_enqueue_hive_for_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
    row=None,
    version_key: str | None = None,
) -> HiveEnqueueResult:
    """``queued``, ``pending`` (indisponible), ``profile_missing`` ou ``miss`` avec ``detail``."""
    resolved = resolve_hive_match_for_item(
        db, settings, media=media, item=item, now=now, row=row, version_key=version_key
    )
    return HiveEnqueueResult(resolved.outcome, resolved.detail)
