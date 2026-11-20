"""Lots B/C — one explicit profile per remote attempt (selection, reingest, Comet, Hive).

Call sites that still resolve ``profile_for_media`` internally are listed in
``NON_WIRED_PROFILE_ATTEMPT_CALL_SITES``. Future orchestration will call the
functions here with a single ``Profile`` per attempt; no profile chain is built
inside this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.quality_profile import Profile, preferences_for
from mediatracker.core.source_select import select_variants
from mediatracker.db.models import Link, Media, MediaItem
from mediatracker.providers.base import EpisodeQuery, LinkCandidate, SourcePreferences, SourceResult

NON_WIRED_PROFILE_ATTEMPT_CALL_SITES: tuple[str, ...] = (
    "mediatracker.core.pipeline.jobs._choose_link (repli pref_* si résolution profil impossible)",
)


@dataclass(frozen=True)
class AttemptSelectionCriteria:
    profile: Profile
    quality: str
    language: str
    allow_fallback: bool
    quality_fallback: tuple[str, ...]
    language_fallback: tuple[str, ...]


def attempt_selection_criteria(profile: Profile) -> AttemptSelectionCriteria:
    """Criteria for one attempt: derived only from the explicit profile."""
    prefs = preferences_for(profile)
    return AttemptSelectionCriteria(
        profile=profile,
        quality=prefs.quality,
        language=prefs.language,
        allow_fallback=prefs.allow_fallback,
        quality_fallback=tuple(prefs.quality_fallback),
        language_fallback=tuple(prefs.language_fallback),
    )


def rank_pages_for_profile_attempt(result: SourceResult, *, profile: Profile) -> int | None:
    from mediatracker.core.quality_profile import acceptance_rank, release_candidate_from_fields

    if result.rejection:
        return None
    return acceptance_rank(
        release_candidate_from_fields(
            language=result.language,
            quality=result.quality,
            hint=result.profile_hint,
            title=result.title,
        ),
        profile,
    )


def select_pages_for_profile_attempt(
    results: list[SourceResult],
    *,
    profile: Profile,
    title: str,
    year: int | None,
    media_type: str,
    seasons: set[int],
    original_title: str | None = None,
    exclude: set[tuple[str | None, str | None]] | None = None,
    tmdb_id: int | None = None,
    trust_bound_urls: bool = False,
) -> list[SourceResult]:
    crit = attempt_selection_criteria(profile)
    return select_variants(
        results,
        title=title,
        original_title=original_title,
        year=year,
        media_type=media_type,
        seasons=seasons,
        quality=crit.quality,
        language=crit.language,
        allow_fallback=crit.allow_fallback,
        quality_fallback=crit.quality_fallback,
        language_fallback=crit.language_fallback,
        exclude=exclude,
        profile=profile,
        tmdb_id=tmdb_id,
        trust_bound_urls=trust_bound_urls,
    )


def reingest_select_pages(
    results: list[SourceResult],
    *,
    media: Media,
    seasons: set[int],
    profile: Profile,
    exclude: set[tuple[str | None, str | None]] | None = None,
) -> list[SourceResult]:
    """Version reingest path for a single explicit profile (no DB profile resolution)."""
    return select_pages_for_profile_attempt(
        results,
        profile=profile,
        title=media.title,
        original_title=media.original_title,
        year=media.year,
        media_type=media.type,
        seasons=seasons,
        exclude=exclude,
    )


def rank_link_for_profile_attempt(link: Link, *, profile: Profile) -> int | None:
    from mediatracker.core.quality_profile import (
        ReleaseCandidate,
        acceptance_rank,
        detect_language,
        normalize_language,
        normalize_resolution,
    )

    if not link.quality:
        return None
    language = normalize_language(link.language) or detect_language(link.filename or "")
    if not language:
        return None
    enforce_size = True
    if link.size is None or link.size <= 0:
        from sqlalchemy import inspect as sa_inspect

        from mediatracker.db.models import Provider

        state = sa_inspect(link, raiseerr=False)
        session = state.session if state is not None else None
        provider_id = getattr(link, "provider_id", None)
        if session is not None and provider_id is not None:
            row = session.get(Provider, provider_id)
            if row is not None and row.type == "manual":
                enforce_size = False
    return acceptance_rank(
        ReleaseCandidate(
            language=language,
            resolution=normalize_resolution(link.quality),
            size=link.size,
            text=link.filename or "",
        ),
        profile,
        enforce_size=enforce_size,
    )


_HOSTER_ATTEMPT_KINDS = frozenset({"magnet", "torrent"})


def link_eligible_for_hoster_attempt(
    link: Link,
    preference: list[str],
    *,
    skipped_link_ids: set[int],
) -> bool:
    """Same hoster eligibility as ``jobs._choose_link`` (dead, kind, skip, hoster)."""
    if link.id is not None and link.id in skipped_link_ids:
        return False
    if link.dead:
        return False
    if link.kind in _HOSTER_ATTEMPT_KINDS:
        return False
    from mediatracker.core.pipeline.jobs import _hoster_ok

    return _hoster_ok(link, preference)


def choose_link_for_profile_attempt(
    preference: list[str],
    *,
    links: Sequence[Link],
    profile: Profile,
    skipped_link_ids: set[int] | None = None,
) -> Link | None:
    """Pick a hoster link for one profile attempt (in-memory links, no DB)."""
    from mediatracker.core.selection import hoster_rank

    skipped = skipped_link_ids or set()
    ranked: list[tuple[int, Link]] = []
    for link in links:
        if not link_eligible_for_hoster_attempt(link, preference, skipped_link_ids=skipped):
            continue
        penalty = rank_link_for_profile_attempt(link, profile=profile)
        if penalty is None:
            continue
        ranked.append((penalty, link))
    if not ranked:
        return None
    ranked.sort(
        key=lambda pair: (
            pair[0],
            hoster_rank(pair[1].hoster, preference),
            -pair[1].priority,
            pair[1].id or 0,
        )
    )
    return ranked[0][1]


def comet_context_for_attempt(*, profile: Profile) -> tuple[Profile, SourcePreferences]:
    """Comet unlock/selection for one explicit profile (no fiche prefs, no CSV)."""
    return profile, preferences_for(profile)


def legacy_comet_context(db: Session, media: Media, settings: Settings) -> tuple[Profile | None, SourcePreferences]:
    """Comet sans profil explicite : fiche pref_* et listes dérivées du profil principal si présent."""
    del settings
    from mediatracker.core.quality_profile import preferences_for, profile_for_media

    spec = profile_for_media(db, media)
    if spec is not None:
        return spec, preferences_for(spec)
    return None, SourcePreferences(quality=media.pref_quality or "HD", language=media.pref_language or "VFF")


def rank_catalog_version_for_profile_attempt(
    version_key: str,
    *,
    profile: Profile,
    size: int | None = None,
    release_text: str | None = None,
) -> int | None:
    """Hive catalogue version rank for one explicit profile (lot C)."""
    from mediatracker.hive.source_catalog import rank_catalog_version_for_profile_attempt as _rank

    return _rank(version_key, profile=profile, size=size, release_text=release_text)


def pick_catalog_match_for_profile_attempt(
    db: Session,
    media: Media,
    item: MediaItem,
    *,
    profile: Profile,
    prefer_available: bool = True,
    version_key: str | None = None,
    skip_bitrate_sanity: bool = False,
    skip_profile_filter: bool = False,
):
    """Hive catalogue selection for one explicit profile (no DB profile resolution)."""
    from mediatracker.hive.source_catalog import pick_catalog_match_for_profile_attempt as _pick

    return _pick(
        db,
        media,
        item,
        profile=profile,
        prefer_available=prefer_available,
        version_key=version_key,
        skip_bitrate_sanity=skip_bitrate_sanity,
        skip_profile_filter=skip_profile_filter,
    )


def select_comet_streams_for_profile_attempt(
    payload: dict,
    query: EpisodeQuery,
    *,
    profile: Profile,
) -> list[LinkCandidate]:
    from mediatracker.providers.sources.comet import select_streams

    prefs = preferences_for(profile)
    return select_streams(payload, query, prefs, profile=profile)
