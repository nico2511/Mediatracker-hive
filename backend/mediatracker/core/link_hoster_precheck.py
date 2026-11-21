"""Reconcile stored link metadata with AllDebrid link/infos before unlock."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from mediatracker.config import Settings
from mediatracker.core.profile_attempt_adapters import manual_provider_link
from mediatracker.core.quality_profile import (
    ReleaseCandidate,
    acceptance_rank,
    detect_language,
    normalize_language,
    normalize_resolution,
    provisional_hd_acceptance_rank,
    release_candidate_from_inspection,
    wawacity_page_hd_label_candidate,
)
from mediatracker.db.models import Download, Link, Media, MediaItem
from mediatracker.providers.base import DebridError, LinkCandidate
from mediatracker.providers.sources.wawacity_html import quality_from_release_name

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mediatracker.core.quality_profile import Profile

logger = logging.getLogger(__name__)


class HosterProfileReject(DebridError):
    """Real file metadata does not match the active profile attempt; skip without unlock."""


@dataclass
class HosterUnlockContext:
    db: Session
    row: Download
    media: Media
    item: MediaItem
    settings: Settings | None
    now: datetime
    source_type: str = "wawacity"
    attempt_profile: Profile | None = None
    attempt_profile_rank: int = 1
    journal_profile_name: str | None = None
    journal_profile_rank: int | None = None


def profile_rank_for_attempt(db: Session, media: Media, settings: Settings, profile: Profile) -> int:
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.profile_fallback import ProfileResolveError

    target_id = profile.quality_profile_id
    if target_id is None:
        return 1
    try:
        chain = load_profile_chain(db, media, settings)
    except ProfileResolveError:
        return 1
    for rank, item in enumerate(chain.profiles, start=1):
        if item.quality_profile_id == target_id:
            return rank
    return 1


def candidate_acceptance_rank(
    candidate: LinkCandidate,
    *,
    profile: Profile,
) -> int | None:
    """Profile rank for one unpersisted candidate (manual page + poll ingestion)."""
    release = release_candidate_from_inspection(
        language=candidate.language,
        quality=candidate.quality,
        filename=candidate.filename or "",
    )
    if wawacity_page_hd_label_candidate(candidate):
        rank = provisional_hd_acceptance_rank(release, profile)
        if rank is not None:
            return rank
    return acceptance_rank(release, profile, enforce_size=False)


def _resolution_from_inspection(filename: str | None, fallback_quality: str | None) -> str | None:
    from_name = quality_from_release_name(filename or "")
    if from_name:
        return normalize_resolution(from_name)
    return normalize_resolution(fallback_quality)


def apply_hoster_inspection_to_link(
    link: Link,
    *,
    filename: str | None,
    size: int | None,
    ctx: HosterUnlockContext | None,
) -> None:
    """After ``decide_hoster`` and before ``unlock``: align link row; check the active attempt only."""
    name = (filename or link.filename or "").strip()
    inspected_resolution = _resolution_from_inspection(name, link.quality)
    inspected_language = normalize_language(link.language) or detect_language(name)

    corrected = False
    if name and name != (link.filename or "").strip():
        link.filename = name
        corrected = True
    if inspected_resolution and inspected_resolution != normalize_resolution(link.quality):
        link.quality = inspected_resolution
        corrected = True
    if inspected_language and inspected_language != normalize_language(link.language):
        link.language = inspected_language
        corrected = True
    if size and size > 0 and link.size != size:
        link.size = size
        corrected = True
    if corrected:
        logger.info(
            "Métadonnées lien mises à jour depuis l'hébergeur (résolution=%s, langue=%s).",
            link.quality,
            link.language,
        )
        if ctx is not None:
            ctx.db.flush()

    if manual_provider_link(link):
        return
    if ctx is None:
        return
    attempt = ctx.attempt_profile
    if attempt is None and ctx.settings is not None:
        from mediatracker.core.profile_attempt_orchestration import download_attempt_profile

        attempt = download_attempt_profile(ctx.db, ctx.media, ctx.settings, ctx.row)
        if attempt is not None:
            ctx.attempt_profile = attempt
            ctx.attempt_profile_rank = profile_rank_for_attempt(ctx.db, ctx.media, ctx.settings, attempt)
    if attempt is not None:
        ctx.journal_profile_name = attempt.name
        ctx.journal_profile_rank = ctx.attempt_profile_rank
    if not name:
        return
    if attempt is None:
        return

    release = ReleaseCandidate(
        language=inspected_language,
        resolution=inspected_resolution,
        size=size or link.size,
        text=name,
    )
    if acceptance_rank(release, attempt, enforce_size=False) is None:
        raise HosterProfileReject("PROFILE", "filtre profil", retryable=False)

    ctx.journal_profile_name = attempt.name
    ctx.journal_profile_rank = ctx.attempt_profile_rank
    profile_id = attempt.quality_profile_id
    if profile_id is not None:
        ctx.row.poll_quality_profile_id = int(profile_id)