"""Worker remote poll: plan (read) → network (no session) → apply (single commit)."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.core.library_guard import absence_updates_allowed
from mediatracker.core.source_poll import (
    CollectPages,
    PollReport,
    _apply,
    _collect,
    _film_is_obtained,
    _finish,
    _gate,
    _mark_binding_error,
    _reconcile_absent_files,
    _remember_page,
    _search,
    _store_report,
    hoster_allowed,
    hoster_rank,
    poll_found_message,
)
from mediatracker.db.models import Media, MediaItem
from mediatracker.providers.base import LinkCandidate, SourceFetchError
from mediatracker.providers.downloaders.aria2 import scrub_url

logger = logging.getLogger(__name__)

_NETWORK_CALLS = 0


def reset_network_call_count() -> None:
    global _NETWORK_CALLS
    _NETWORK_CALLS = 0


def network_call_count() -> int:
    return _NETWORK_CALLS


@dataclass
class RemoteCollectPlan:
    """Frozen outcome of the read phase; safe to carry across sessions."""

    media_id: int
    provider_id: int
    force: bool
    moment: datetime
    # Early finish without HTTP (apply still runs _finish once).
    early: PollReport | None = None
    # Network payload (empty when early is set).
    pages: list = field(default_factory=list)
    report_rows: list = field(default_factory=list)
    slots: list = field(default_factory=list)
    preference: list = field(default_factory=list)
    use_bindings: bool = True
    repair_reread: bool = False
    fetch_error: SourceFetchError | None = None
    needs_search: bool = False
    seasons: set[int] | None = None
    quality: str = ""
    language: str = ""
    allow_fallback: bool = False
    quality_fallback: list = field(default_factory=list)
    language_fallback: list = field(default_factory=list)
    media_title: str = ""
    media_original_title: str | None = None
    media_year: int | None = None
    media_type: str = ""
    profile_name: str | None = None
    profile_id: int | None = None
    profile: object | None = None
    from_bindings: bool = False
    binding_error_page: object | None = None
    binding_error_message: str = ""
    schedule_finish: bool = True
    single_profile: object | None = None
    profile_rank: int = 1


@dataclass
class RemoteCollectResult:
    plan: RemoteCollectPlan
    links: list[LinkCandidate] = field(default_factory=list)


def plan_remote_poll(
    db: Session,
    media: Media,
    items: list[MediaItem],
    provider_id: int,
    source: object,
    settings: Settings,
    moment: datetime,
    force: bool,
    rng: random.Random,
    *,
    use_bindings: bool = True,
    repair_reread: bool = False,
    single_profile: object | None = None,
    schedule_finish: bool = True,
) -> RemoteCollectPlan:
    """Read-only planning; may reconcile files but commits with caller."""
    plan = RemoteCollectPlan(
        media_id=media.id,
        provider_id=provider_id,
        force=force,
        moment=moment,
        use_bindings=use_bindings,
        repair_reread=repair_reread,
    )
    plan.schedule_finish = schedule_finish
    plan.single_profile = single_profile
    if _film_is_obtained(db, media, items):
        plan.early = PollReport(
            result="obtained",
            message="Obtenu.",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=media.last_source_poll_at or moment,
            next_check_at=None,
        )
        return plan
    if not force:
        allowed, _reason = _gate(db, settings, media, items, moment)
        if not allowed:
            plan.early = PollReport(
                result="skipped",
                message="",
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=media.last_source_poll_at or moment,
                next_check_at=media.next_source_poll_at or moment,
            )
            return plan
    try:
        report = _collect(
            db,
            media,
            items,
            provider_id,
            source,
            settings,
            moment,
            force,
            rng,
            use_bindings=use_bindings,
            repair_reread=repair_reread,
            commit=False,
            network_only=True,
            single_profile=single_profile,
            schedule_finish=schedule_finish,
        )
    except SourceFetchError as exc:
        plan.fetch_error = exc
        return plan
    if isinstance(report, PollReport):
        plan.early = report
        return plan
    assert isinstance(report, CollectPages)
    plan.pages = report.pages
    plan.report_rows = report.report_rows
    plan.slots = report.slots
    plan.preference = report.preference
    plan.needs_search = report.needs_search
    plan.seasons = report.seasons
    plan.quality = report.quality
    plan.language = report.language
    plan.allow_fallback = report.allow_fallback
    plan.quality_fallback = report.quality_fallback
    plan.language_fallback = report.language_fallback
    plan.media_title = report.media_title
    plan.media_original_title = report.media_original_title
    plan.media_year = report.media_year
    plan.media_type = report.media_type
    plan.profile_name = report.profile_name
    plan.profile_id = report.profile_id
    plan.profile = report.profile
    plan.from_bindings = report.from_bindings
    plan.profile_rank = report.profile_rank
    return plan


def _run_search_phase(plan: RemoteCollectPlan, source: object, settings: Settings) -> None:
    global _NETWORK_CALLS
    from types import SimpleNamespace

    from mediatracker.core.profile_attempt_adapters import attempt_selection_criteria, select_pages_for_profile_attempt
    from mediatracker.core.quality_profile import Profile, preferences_for
    from mediatracker.core.source_select import explain_misses

    _NETWORK_CALLS += 1
    media = SimpleNamespace(
        title=plan.media_title,
        original_title=plan.media_original_title,
        year=plan.media_year,
        type=plan.media_type,
    )
    seasons = plan.seasons or set()
    spec: Profile | None = plan.profile if isinstance(plan.profile, Profile) else None
    if spec is None:
        plan.fetch_error = SourceFetchError("PROFILE", "Profil introuvable pour ce sondage.")
        return
    crit = attempt_selection_criteria(spec)
    preferences = preferences_for(spec)
    try:
        results = _search(
            source,
            media,
            seasons,
            crit.quality,
            crit.language,
            crit.allow_fallback,
            None,
            settings,
            preferences=preferences,
        )
    except SourceFetchError as exc:
        plan.fetch_error = exc
        return
    pages = select_pages_for_profile_attempt(
        list(results),
        profile=spec,
        title=plan.media_title,
        original_title=plan.media_original_title,
        year=plan.media_year,
        media_type=plan.media_type,
        seasons=seasons,
    )
    if plan.media_type == "film":
        pages = pages[:1]
    else:
        pages = pages[:4]
    plan.report_rows = list(results)
    plan.pages = pages
    if not pages:
        detail = explain_misses(
            list(results),
            title=plan.media_title,
            original_title=plan.media_original_title,
            year=plan.media_year,
            media_type=plan.media_type,
            seasons=seasons,
            quality=crit.quality,
            language=crit.language,
            allow_fallback=crit.allow_fallback,
            quality_fallback=list(crit.quality_fallback),
            language_fallback=list(crit.language_fallback),
            profile=spec,
        )
        message = f"Aucune version ne correspond au profil « {spec.name} »."
        if detail:
            message = f"{message} {detail}"
        plan.early = PollReport(
            result="nothing",
            message=message,
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=plan.moment,
            next_check_at=plan.moment,
        )


def collect_remote_network(
    plan: RemoteCollectPlan,
    source: object,
    settings: Settings,
) -> RemoteCollectResult:
    """HTTP only; no SQLAlchemy session."""
    global _NETWORK_CALLS
    if plan.early is not None or plan.fetch_error is not None:
        return RemoteCollectResult(plan=plan)
    if plan.needs_search:
        _run_search_phase(plan, source, settings)
        if plan.early is not None:
            return RemoteCollectResult(plan=plan)
    links: list[LinkCandidate] = []
    binding_retry = False
    page_index = 0
    while page_index < len(plan.pages):
        page = plan.pages[page_index]
        _NETWORK_CALLS += 1
        try:
            from mediatracker.core.source_poll import _page_links

            links.extend(_page_links(source, page, plan.profile))
            page_index += 1
        except SourceFetchError as exc:
            if plan.from_bindings and not binding_retry:
                plan.binding_error_page = page
                plan.binding_error_message = exc.message
                plan.from_bindings = False
                binding_retry = True
                plan.needs_search = True
                _run_search_phase(plan, source, settings)
                if plan.fetch_error is not None or plan.early is not None:
                    return RemoteCollectResult(plan=plan, links=links)
                page_index = 0
                continue
            plan.fetch_error = exc
            break
    kept = [link for link in links if hoster_allowed(link.hoster, plan.preference)]
    kept.sort(
        key=lambda link: (link.season or 0, link.episode or 0, hoster_rank(link.hoster, plan.preference), link.url)
    )
    return RemoteCollectResult(plan=plan, links=kept)


def apply_remote_collect(
    db: Session,
    settings: Settings,
    collected: RemoteCollectResult,
    rng: random.Random,
) -> PollReport | None:
    """Re-read guards and persist one atomic unit (flush; commit with caller)."""
    media = db.get(Media, collected.plan.media_id)
    if media is None or not media.followed or media.on_hold:
        return None
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    plan = collected.plan
    moment = plan.moment
    force = plan.force
    schedule = plan.schedule_finish
    if absence_updates_allowed(db):
        _reconcile_absent_files(db, media, items, settings, moment)
    allowed, _reason = _gate(db, settings, media, items, moment)
    if not allowed and not force:
        return _finish(
            db,
            media,
            settings,
            moment,
            rng,
            result="skipped",
            message="",
            episodes=0,
            queued=0,
            proposed=0,
            failed=False,
            force=force,
            schedule=schedule,
        )
    if plan.binding_error_page is not None:
        _mark_binding_error(
            db,
            media.id,
            plan.provider_id,
            plan.binding_error_page,
            moment,
            plan.binding_error_message,
        )
    if plan.early is not None:
        if plan.early.result == "obtained":
            media.next_source_poll_at = None
            media.last_poll_result = "obtained"
            media.last_poll_message = "Obtenu."
        return _finish(
            db,
            media,
            settings,
            moment,
            rng,
            result=plan.early.result,
            message=plan.early.message,
            episodes=plan.early.episodes,
            queued=plan.early.queued,
            proposed=plan.early.proposed,
            failed=False,
            force=force,
            schedule=schedule,
        )
    if plan.fetch_error is not None:
        exc = plan.fetch_error
        configured = exc.code != "NOT_CONFIGURED"
        return _finish(
            db,
            media,
            settings,
            moment,
            rng,
            result="error",
            message=scrub_url(exc.message)[:300],
            episodes=0,
            queued=0,
            proposed=0,
            failed=configured and exc.retryable,
            force=force,
            schedule=schedule,
        )
    _store_report(media, plan.report_rows, plan.pages)
    for page in plan.pages:
        _remember_page(
            db,
            media.id,
            plan.provider_id,
            page,
            moment,
            media=media,
            items=items,
            profile_rank=plan.profile_rank,
        )
    queued, proposed, episodes = _apply(
        db,
        media,
        items,
        plan.slots,
        collected.links,
        plan.provider_id,
        plan.preference,
        moment,
        poll_quality_profile_id=plan.profile_id,
    )
    auto = media.new_episode_policy == "auto"
    message = poll_found_message(media, episodes=episodes, queued=queued, auto=auto)
    result = "nothing" if episodes == 0 else "found"
    return _finish(
        db,
        media,
        settings,
        moment,
        rng,
        result=result,
        message=message,
        episodes=episodes,
        queued=queued,
        proposed=proposed,
        failed=False,
        force=force,
        schedule=schedule,
    )


def worker_remote_poll(
    factory: sessionmaker[Session],
    settings: Settings,
    media_id: int,
    provider_id: int,
    source: object,
    moment: datetime,
    draw: random.Random,
    *,
    force: bool = False,
    single_profile: object | None = None,
    schedule_finish: bool = True,
    use_bindings: bool = True,
    repair_reread: bool = False,
) -> PollReport | None:
    """Full worker remote path: plan session → network → apply_with_retry (apply only)."""
    from mediatracker.db.session import apply_with_retry, commit_retry

    read = factory()
    try:
        media = read.get(Media, media_id)
        if media is None:
            return None
        items = list(read.scalars(select(MediaItem).where(MediaItem.media_id == media_id)).all())
        plan = plan_remote_poll(
            read,
            media,
            items,
            provider_id,
            source,
            settings,
            moment,
            force,
            draw,
            single_profile=single_profile,
            schedule_finish=schedule_finish,
            use_bindings=use_bindings,
            repair_reread=repair_reread,
        )
        commit_retry(read)
    finally:
        read.close()
    collected = collect_remote_network(plan, source, settings)

    def _apply(db: Session):
        return apply_remote_collect(db, settings, collected, draw)

    return apply_with_retry(factory, "Application sondage source", _apply)
