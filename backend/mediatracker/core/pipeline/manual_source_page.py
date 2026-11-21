"""Page source en saisie manuelle : priorité avant la cascade, filtre profil + replis."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.hosters import source_ingestion_hosters
from mediatracker.core.quality_profile import Profile
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, hoster_rank
from mediatracker.core.source_binding import binding_is_usable, binding_page_url
from mediatracker.db.models import Link, Media, MediaItem, Provider, SourceBinding
from mediatracker.providers.base import LinkCandidate, SourceRef

logger = logging.getLogger(__name__)


def manual_path_binding_for_item(db: Session, media: Media, item: MediaItem) -> SourceBinding | None:
    """Active binding explicitly set by the user on the maintenance panel."""
    if media.type == "series":
        if item.season is None:
            return None
        season = item.season
    else:
        season = None
    stmt = select(SourceBinding).where(
        SourceBinding.media_id == media.id,
        SourceBinding.path_manual.is_(True),
        SourceBinding.active.is_(True),
    )
    if media.type == "series":
        stmt = stmt.where(SourceBinding.season == season)
    else:
        stmt = stmt.where(SourceBinding.season.is_(None))
    for binding in db.scalars(stmt).all():
        if binding_is_usable(binding) and (binding.path or binding.url):
            return binding
    return None


def episode_candidates(candidates: list[LinkCandidate], item: MediaItem) -> list[LinkCandidate]:
    episode = item.episode
    return [row for row in candidates if row.episode == episode]


def _stored_link_for_candidate(db: Session, provider_id: int, external_id: str) -> Link | None:
    return db.scalar(
        select(Link).where(Link.provider_id == provider_id, Link.external_id == external_id)
    )


def _candidate_attemptable(
    db: Session,
    provider_id: int,
    candidate: LinkCandidate,
    media_item_id: int,
    *,
    skipped_link_ids: set[int],
) -> bool:
    existing = _stored_link_for_candidate(db, provider_id, candidate.external_id)
    if existing is None:
        return True
    if existing.media_item_id is not None and existing.media_item_id != media_item_id:
        return False
    if existing.dead or existing.id in skipped_link_ids:
        return False
    return True


def pick_manual_source_page_link(
    db: Session,
    provider_id: int,
    profiles: Sequence[Profile],
    candidates: list[LinkCandidate],
    item: MediaItem,
    preference: list[str],
    *,
    skipped_link_ids: set[int] | frozenset[int] | None = None,
) -> tuple[list[LinkCandidate], Profile, int] | None:
    """First profile in chain that accepts at least one episode link; best links first."""
    skipped = {int(value) for value in (skipped_link_ids or [])}
    rows = episode_candidates(candidates, item)
    if not rows:
        return None
    for rank, profile in enumerate(profiles, start=1):
        ranked: list[tuple[int, int, str, LinkCandidate]] = []
        for candidate in rows:
            if not _candidate_attemptable(
                db, provider_id, candidate, item.id, skipped_link_ids=skipped
            ):
                continue
            hoster = (candidate.hoster or "").casefold()
            if hoster in BLOCKED_SOURCE_HOSTERS:
                continue
            from mediatracker.core.link_hoster_precheck import candidate_acceptance_rank

            penalty = candidate_acceptance_rank(candidate, profile=profile)
            if penalty is None:
                continue
            ranked.append((penalty, hoster_rank(candidate.hoster, preference), candidate.url, candidate))
        if not ranked:
            continue
        ranked.sort(key=lambda pair: (pair[0], pair[1], pair[2]))
        ordered = [pair[3] for pair in ranked]
        return ordered, profile, rank
    return None


def fetch_manual_binding_link_candidates(
    db: Session,
    settings: Settings,
    binding: SourceBinding,
) -> list[LinkCandidate]:
    from mediatracker.providers.sources.loading import close_source, remote_links_source

    provider = db.get(Provider, binding.provider_id)
    if provider is None:
        return []
    opened = remote_links_source(db, settings, provider.type)
    if opened is None:
        return []
    _provider_id, source = opened
    page_url = binding_page_url(db, settings, binding)
    if not page_url:
        close_source(source)
        return []
    ref = SourceRef(ref=(binding.external_ref or page_url).strip(), url=page_url)
    ingestion_hosters = list(source_ingestion_hosters(None))
    try:
        return list(source.get_links(ref, hoster_preference=ingestion_hosters))
    finally:
        close_source(source)


def link_for_manual_page_candidate(
    db: Session,
    item: MediaItem,
    candidate: LinkCandidate,
    provider_id: int,
    preference: list[str],
    now: datetime,
) -> Link | None:
    existing = db.scalar(
        select(Link).where(Link.provider_id == provider_id, Link.external_id == candidate.external_id)
    )
    if existing is not None:
        if existing.dead:
            return existing
        if existing.media_item_id is not None and existing.media_item_id != item.id:
            return None
        return existing
    row = Link(
        media_item_id=item.id,
        provider_id=provider_id,
        kind=candidate.kind,
        url=candidate.url,
        hoster=candidate.hoster,
        quality=candidate.quality,
        language=candidate.language,
        filename=candidate.filename[:500] if candidate.filename else None,
        external_id=candidate.external_id,
        priority=max(0, 100 - hoster_rank(candidate.hoster, preference)),
        discovered_at=now,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        logger.info(
            "Page source manuelle : conflit uq_link_external external_id=%s item=%s type=IntegrityError",
            candidate.external_id,
            item.id,
        )
        existing = _stored_link_for_candidate(db, provider_id, candidate.external_id)
        if existing is None:
            raise
        if existing.dead:
            return existing
        if existing.media_item_id is not None and existing.media_item_id != item.id:
            return None
        return existing
    return row