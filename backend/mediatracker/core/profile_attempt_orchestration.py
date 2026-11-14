"""Lot E — compose lot A resolution with B/C adapters (profile-first, then source order).

No HTTP, no CSV ``pref_*`` / quality_fallback chains: each attempt uses one explicit profile.

**Contexte profil sur les téléchargements** (``Download.poll_quality_profile_id``) :
- Identité stable : ID ``quality_profile``, pas le nom (renommage libre sans casser la file).
- ``NULL`` : jobs legacy / hors sondage → principal via chaîne A à l'unlock.
- ID renseigné mais ligne absente : erreur ``POLL_PROFILE_MISSING`` (pas de repli principal ni CSV Comet).
- Modifier les critères d'un profil existant s'applique aux jobs qui pointent encore ce même ID.
- Suppression d'un profil référencé par la file : refusée par l'API (pas de SET NULL silencieux).
- L'identité est transportée via ``Profile.quality_profile_id`` depuis la résolution A (pas de re-lookup par nom).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.profile_attempt import (
    ProfileSourceAttempt,
    iter_profile_source_attempts,
    walk_profile_source_attempts,
)
from mediatracker.core.profile_attempt_adapters import (
    pick_catalog_match_for_profile_attempt,
    select_pages_for_profile_attempt,
)
from mediatracker.core.profile_fallback import ProfileResolveError, resolve_profiles_for_media
from mediatracker.core.quality_profile import Profile, profile_from_row
from mediatracker.db.models import Download, Media, MediaItem, QualityProfile
from mediatracker.hive.source_catalog import HiveCatalogMatch
from mediatracker.providers.base import SourceResult

if TYPE_CHECKING:
    from mediatracker.core.source_poll import PollReport

__all__ = [
    "HiveProfilePick",
    "ProfileChain",
    "ProfileWalkTrail",
    "RemotePagePick",
    "active_hive_fetch_sha_for_item",
    "PollProfileResolution",
    "POLL_PROFILE_MISSING",
    "download_attempt_profile",
    "download_source_order",
    "resolve_download_poll_profile",
    "load_profile_chain",
    "pick_hive_catalog_for_item",
    "pick_remote_pages_for_profiles",
    "orchestrate_profile_first_poll",
    "poll_media_profile_first",
    "walk_profile_attempt_trail",
]

_ACTIVE_HIVE_JOB = frozenset({"pending", "downloading", "paused", "verifying"})
POLL_PROFILE_MISSING = "POLL_PROFILE_MISSING"


@dataclass(frozen=True)
class PollProfileResolution:
    profile: Profile | None
    explicit: bool
    missing_explicit: bool


@dataclass(frozen=True)
class ProfileChain:
    profiles: tuple[Profile, ...]

    @property
    def principal(self) -> Profile:
        return self.profiles[0]


@dataclass(frozen=True)
class HiveProfilePick:
    match: HiveCatalogMatch
    profile: Profile
    frozen_sha256: str | None


@dataclass(frozen=True)
class RemotePagePick:
    pages: list[SourceResult]
    profile: Profile


@dataclass(frozen=True)
class ProfileWalkTrail:
    """Discriminant trace labels P{n}S{m} for tests (1-based profile and source indexes)."""

    labels: tuple[str, ...]
    chosen: ProfileSourceAttempt | None


def load_profile_chain(db: Session, media: object, settings: Settings | None = None) -> ProfileChain:
    resolved = resolve_profiles_for_media(db, media, settings)
    return ProfileChain(profiles=tuple(item.profile for item in resolved))


def profiles_for_poll_attempt(
    db: Session,
    media: object,
    settings: Settings,
    *,
    single_profile: Profile | None = None,
) -> tuple[ProfileChain | None, str | None]:
    if single_profile is not None:
        if single_profile.quality_profile_id is None:
            return None, "Profil sans identité stable (quality_profile_id manquant)."
        return ProfileChain(profiles=(single_profile,)), None
    try:
        chain = load_profile_chain(db, media, settings)
    except ProfileResolveError as exc:
        return None, str(exc)
    return chain, None


def resolve_download_poll_profile(
    db: Session,
    media: Media,
    settings: Settings,
    download: Download,
) -> PollProfileResolution:
    """Résout le profil pour la file/unlock ; contexte explicite introuvable = erreur."""
    pid = getattr(download, "poll_quality_profile_id", None)
    if pid is not None:
        row = db.get(QualityProfile, int(pid))
        if row is None:
            return PollProfileResolution(profile=None, explicit=True, missing_explicit=True)
        return PollProfileResolution(
            profile=profile_from_row(row),
            explicit=True,
            missing_explicit=False,
        )
    try:
        principal = load_profile_chain(db, media, settings).principal
    except ProfileResolveError:
        return PollProfileResolution(profile=None, explicit=False, missing_explicit=False)
    return PollProfileResolution(profile=principal, explicit=False, missing_explicit=False)


def download_attempt_profile(
    db: Session,
    media: Media,
    settings: Settings,
    download: Download,
) -> Profile | None:
    """Convenience : profil utilisable ; ``None`` si absent ou contexte explicite perdu."""
    resolved = resolve_download_poll_profile(db, media, settings, download)
    if resolved.missing_explicit:
        return None
    return resolved.profile


def download_source_order(db: Session, media: Media, settings: Settings) -> tuple[str, ...]:
    from mediatracker.core.pipeline.jobs import _download_sources
    from mediatracker.core.source_availability import filter_usable_source_order

    return tuple(filter_usable_source_order(db, settings, _download_sources(db, media, settings)))


def active_hive_fetch_sha_for_item(db: Session, media: Media, item: MediaItem) -> str | None:
    """SHA d'un téléchargement Hive aria2 actif pour ce slot."""
    del media
    if item.id is None:
        return None
    from mediatracker.db.models import DownloadHiveFetch

    row = db.scalar(
        select(DownloadHiveFetch.sha256_hex)
        .join(Download, Download.id == DownloadHiveFetch.download_id)
        .where(
            Download.media_item_id == item.id,
            Download.state.in_(("pending", "resolving", "queued", "downloading", "post_processing")),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )
    return str(row).lower() if row else None


def pick_hive_catalog_for_item(
    db: Session,
    media: Media,
    item: MediaItem,
    chain: ProfileChain,
    *,
    prefer_available: bool = True,
    frozen_sha256: str | None = None,
    single_profile: Profile | None = None,
    version_key: str | None = None,
    explicit_version: bool = False,
) -> HiveProfilePick | None:
    profiles: Sequence[Profile] = (single_profile,) if single_profile is not None else chain.profiles
    if explicit_version:
        frozen = ""
    elif frozen_sha256 is not None:
        frozen = frozen_sha256
    else:
        frozen = active_hive_fetch_sha_for_item(db, media, item)
    for profile in profiles:
        match = pick_catalog_match_for_profile_attempt(
            db,
            media,
            item,
            profile=profile,
            prefer_available=prefer_available,
            version_key=version_key,
            skip_bitrate_sanity=explicit_version,
        )
        if match is None:
            continue
        if frozen and match.sha256_hex != frozen:
            continue
        return HiveProfilePick(match=match, profile=profile, frozen_sha256=frozen)
    return None


def pick_remote_pages_for_profiles(
    results: list[SourceResult],
    *,
    media: Media,
    seasons: set[int],
    chain: ProfileChain,
    exclude: set[tuple[str | None, str | None]] | None = None,
    single_profile: Profile | None = None,
) -> RemotePagePick | None:
    profiles: Sequence[Profile] = (single_profile,) if single_profile is not None else chain.profiles
    for profile in profiles:
        pages = select_pages_for_profile_attempt(
            list(results),
            profile=profile,
            title=media.title,
            original_title=media.original_title,
            year=media.year,
            media_type=media.type,
            seasons=seasons,
            exclude=exclude,
        )
        if pages:
            return RemotePagePick(pages=pages, profile=profile)
    return None


def walk_profile_attempt_trail(
    chain: ProfileChain,
    source_order: Sequence[str],
    *,
    accepts,
) -> ProfileWalkTrail:
    order = tuple(source_order)
    found, trail = walk_profile_source_attempts(chain.profiles, order, accepts=accepts)
    labels: list[str] = []
    for step in trail:
        labels.append(f"P{step.profile_index + 1}S{order.index(step.source) + 1}")
    return ProfileWalkTrail(labels=tuple(labels), chosen=found)


def _poll_attempt_success(report: PollReport | None) -> bool:
    if report is None:
        return False
    if report.queued > 0:
        return True
    if report.result in {"found", "obtained"} and report.episodes > 0:
        return True
    return False


def _item_served_by_hoster_profile(
    db: Session,
    settings: Settings,
    media_item_id: int,
    profile: Profile,
) -> bool:
    """Lien hoster sur l'item compatible avec le profil (tentative distante)."""
    from sqlalchemy import select

    from mediatracker.core.pipeline.jobs import _hosters
    from mediatracker.core.profile_attempt_adapters import choose_link_for_profile_attempt
    from mediatracker.db.models import Link

    if profile.quality_profile_id is None:
        return False
    preference = _hosters(db, settings)
    links = list(
        db.scalars(select(Link).where(Link.media_item_id == media_item_id, Link.dead.is_(False))).all()
    )
    return (
        choose_link_for_profile_attempt(
            preference,
            links=links,
            profile=profile,
            skipped_link_ids=set(),
        )
        is not None
    )


def _item_served_by_hive_profile(
    db: Session,
    media: Media,
    item: MediaItem,
    profile: Profile,
) -> bool:
    """Transfert Hive actif pour le slot + critères profil sur le SHA exact file (pas re-pick)."""
    from mediatracker.hive.source_catalog import catalog_sha_on_item_matches_profile

    active_sha = active_hive_fetch_sha_for_item(db, media, item)
    if active_sha is None:
        return False
    return catalog_sha_on_item_matches_profile(db, media, item, active_sha, profile=profile)


def _item_served_for_attempt_scope(
    db: Session,
    settings: Settings,
    media_item_id: int,
    profile: Profile,
    *,
    source: str,
) -> bool:
    """Validation du service selon la source de la tentative (Hive vs distant)."""
    item = db.get(MediaItem, media_item_id)
    if item is None:
        return False
    media = db.get(Media, item.media_id)
    if media is None:
        return False
    if source == "hive":
        return _item_served_by_hive_profile(db, media, item, profile)
    return _item_served_by_hoster_profile(db, settings, media_item_id, profile)


def _item_served_for_attach_scope(
    db: Session,
    settings: Settings,
    media_item_id: int,
    profile: Profile,
) -> bool:
    """Post-succès rediscovery : hoster ou transfert Hive actif pour l'item cible."""
    item = db.get(MediaItem, media_item_id)
    if item is None:
        return False
    media = db.get(Media, item.media_id)
    if media is None:
        return False
    if _item_served_by_hoster_profile(db, settings, media_item_id, profile):
        return True
    return _item_served_by_hive_profile(db, media, item, profile)


def orchestrate_profile_first_poll(
    factory,
    settings: Settings,
    media_id: int,
    *,
    force: bool = False,
    now=None,
    rng=None,
    use_bindings: bool = True,
    repair_reread: bool = False,
    restrict_single_profile: Profile | None = None,
    attach_profile_download_id: int | None = None,
    attach_profile_item_id: int | None = None,
) -> PollReport | None:
    """Profil×source via collecte hors session ; apply_with_retry n'exécute que l'application."""
    import random
    from datetime import datetime, timezone

    from mediatracker.core.source_poll import _finish, close_source
    from mediatracker.core.source_poll_phased import worker_remote_poll
    from mediatracker.db.session import apply_with_retry, commit_retry
    from mediatracker.hive.identity import hive_enabled_in_db
    from mediatracker.hive.source_poll import run_hive_follow_poll
    from mediatracker.providers.sources.loading import remote_links_source

    moment = now or datetime.now(timezone.utc)
    draw = rng or random.Random()
    read = factory()
    try:
        media = read.get(Media, media_id)
        if media is None or not media.followed:
            return None
        if restrict_single_profile is not None:
            if restrict_single_profile.quality_profile_id is None:
                return None
            chain = ProfileChain(profiles=(restrict_single_profile,))
        else:
            try:
                chain = load_profile_chain(read, media, settings)
            except ProfileResolveError as exc:
                resolve_message = str(exc)

                def _profile_err(db: Session, message: str = resolve_message):
                    row = db.get(Media, media_id)
                    if row is None:
                        return None
                    return _finish(
                        db,
                        row,
                        settings,
                        moment,
                        draw,
                        result="error",
                        message=message,
                        episodes=0,
                        queued=0,
                        proposed=0,
                        failed=True,
                        force=force,
                    )

                return apply_with_retry(factory, "Erreur profil", _profile_err)
        order = download_source_order(read, media, settings)
        hive_on = hive_enabled_in_db(read)
        commit_retry(read)
    finally:
        read.close()

    last: PollReport | None = None
    chosen: PollReport | None = None
    had_error = False
    winning_profile_id: int | None = None
    winning_profile: Profile | None = None
    scoped_attach = attach_profile_download_id is not None and attach_profile_item_id is not None

    for attempt in iter_profile_source_attempts(chain.profiles, order):
        if attempt.source == "hive":
            if not hive_on:
                continue

            attempt_profile = attempt.profile

            def _hive_apply(db: Session, profile=attempt_profile):
                row = db.get(Media, media_id)
                if row is None or not row.followed:
                    return None
                return run_hive_follow_poll(
                    db,
                    row,
                    settings,
                    now=moment,
                    rng=draw,
                    force=force,
                    commit=False,
                    single_profile=profile,
                    schedule_finish=False,
                )

            report = apply_with_retry(factory, "Application Hive", _hive_apply)
        else:
            prep = factory()
            try:
                opened = remote_links_source(prep, settings, attempt.source)
            finally:
                prep.close()
            if opened is None and attempt.source not in {"comet"}:
                continue
            if opened is None:
                continue
            provider_id, source = opened
            try:
                report = worker_remote_poll(
                    factory,
                    settings,
                    media_id,
                    provider_id,
                    source,
                    moment,
                    draw,
                    force=force,
                    single_profile=attempt.profile,
                    schedule_finish=False,
                    use_bindings=use_bindings,
                    repair_reread=repair_reread,
                )
            finally:
                close_source(source)
        if report is None:
            continue
        last = report
        if report.result == "error":
            last = report
            had_error = True
            continue
        if _poll_attempt_success(report):
            if scoped_attach:
                probe = factory()
                try:
                    served = _item_served_for_attempt_scope(
                        probe,
                        settings,
                        int(attach_profile_item_id),
                        attempt.profile,
                        source=attempt.source,
                    )
                finally:
                    probe.close()
                if not served:
                    last = report
                    continue
            chosen = report
            winning_profile = attempt.profile
            winning_profile_id = attempt.profile.quality_profile_id
            break

    def _finalize(db: Session):
        row = db.get(Media, media_id)
        if row is None:
            return last
        final = chosen or last
        if final is None:
            return None
        return _finish(
            db,
            row,
            settings,
            moment,
            draw,
            result=final.result,
            message=final.message,
            episodes=final.episodes,
            queued=final.queued,
            proposed=final.proposed,
            failed=final.result == "error" and had_error,
            force=force,
            schedule=True,
        )

    result = apply_with_retry(factory, "Finalisation sondage profils×sources", _finalize)

    if attach_profile_download_id is not None and winning_profile_id is not None and winning_profile is not None:

        def _attach_download_profile(db: Session):
            from mediatracker.db.models import Download, MediaItem

            row = db.get(Download, int(attach_profile_download_id))
            if row is None or row.poll_quality_profile_id is not None:
                return
            if attach_profile_item_id is not None and row.media_item_id != int(attach_profile_item_id):
                return
            item = db.get(MediaItem, row.media_item_id)
            if item is None:
                return
            media_row = db.get(Media, item.media_id)
            if media_row is None:
                return
            resolution = resolve_download_poll_profile(db, media_row, settings, row)
            if resolution.missing_explicit or resolution.explicit:
                return
            if attach_profile_item_id is not None and not _item_served_for_attach_scope(
                db, settings, int(attach_profile_item_id), winning_profile
            ):
                return
            row.poll_quality_profile_id = int(winning_profile_id)

        apply_with_retry(factory, "Contexte profil téléchargement rediscovery", _attach_download_profile)

    return result


def poll_media_profile_first(
    db: Session,
    media: Media,
    settings: Settings,
    *,
    force: bool = False,
    now=None,
    rng=None,
    commit: bool = True,
    factory=None,
) -> PollReport | None:
    """Wrapper : exige une session_factory pour respecter le contrat PR181."""
    if factory is not None:
        return orchestrate_profile_first_poll(
            factory,
            settings,
            media.id,
            force=force,
            now=now,
            rng=rng,
        )
    raise RuntimeError("poll_media_profile_first requiert factory= (chemin phased)")
