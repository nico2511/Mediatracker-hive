"""Poll followed media through a source module, then hand links to the download queue.

The source is any object with ``search`` and ``get_links``. Wawacity is one
implementation. Protected links are stored as-is: AllDebrid's redirector runs
later, inside the existing pipeline, and only when a download actually starts.
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from mediatracker.config import Settings
from mediatracker.core.forget import forget_local_file, is_voluntarily_deleted
from mediatracker.core.library_guard import absence_updates_allowed, is_suspended
from mediatracker.core.pipeline.download_guard import (
    active_download_for_item,
)
from mediatracker.core.polling import EpisodeAir, PollSubject, should_poll_source
from mediatracker.core.selection import (
    DEFAULT_HOSTER_PREFERENCE,
    hoster_allowed,
    hoster_rank,
    parse_hoster_preference,
)
from mediatracker.core.source_binding import (
    clear_auto_binding_path,
    pick_bound_pages,
    remember_binding_fields,
    should_persist_auto_binding,
)
from mediatracker.db.models import Download, LibraryFile, Link, Media, MediaItem, Setting, SourceBinding
from mediatracker.db.session import commit_retry, release_sqlite_before_network
from mediatracker.events import record_event
from mediatracker.providers.base import LinkCandidate, SourceFetchError, SourceRef
from mediatracker.providers.downloaders.aria2 import scrub_url

logger = logging.getLogger(__name__)

# A finished job is not in this list: a missing file may be queued once more.
_MAX_PAGES = 4
_BACKOFF_CAP_MIN = 1440
_UNCHANGED_BACKOFF_CAP_MULT = 8
_POLL_CANDIDATE_BATCH = 50
_SCHEDULE_FLOOR = datetime(1, 1, 1)


@dataclass(frozen=True)
class Slot:
    season: int | None
    episode: int | None
    item_id: int | None


@dataclass
class CollectPages:
    """Pages and slots chosen during planning; network fetches links afterward."""

    pages: list
    report_rows: list
    slots: list
    preference: list
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
    profile_rank: int = 1


@dataclass(frozen=True)
class PollReport:
    result: str
    message: str
    episodes: int
    queued: int
    proposed: int
    last_check_at: datetime
    next_check_at: datetime | None


def poll_found_message(media: Media, *, episodes: int, queued: int, auto: bool) -> str:
    """Libellé affiché après contrôle d'une page source."""
    if media.type == "film":
        if episodes == 0:
            return "La page ne contient pas le film attendu."
        if auto:
            return "Film disponible, mis en file."
        return "Film disponible, en attente de confirmation."
    if episodes == 0:
        return "La page ne contient pas les épisodes attendus."
    if auto:
        noun = "épisode" if episodes == 1 else "épisodes"
        verb = "disponible" if episodes == 1 else "disponibles"
        return f"{episodes} {noun} {verb}, {queued} mis en file."
    return (
        f"{episodes} épisode{'s' if episodes > 1 else ''} disponible{'s' if episodes > 1 else ''}, "
        "en attente de confirmation."
    )


def maybe_poll_sources(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    sources: list[tuple[int, object]] | None = None,
    rng: random.Random | None = None,
    commit: bool = True,
) -> PollReport | None:
    """One due media, if the gate is open. The caller owns ``sources`` when it passes them."""
    moment = now or datetime.now(timezone.utc)
    media = next_due(db, settings, moment, commit=commit)
    if media is None or not sources:
        return None
    provider_id, source = sources[0]
    return run_poll(
        db,
        media,
        provider_id,
        source,
        settings,
        now=moment,
        rng=rng,
        force=False,
        commit=commit,
    )


def _persist(db: Session, *, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def next_due(db: Session, settings: Settings, now: datetime, *, commit: bool = True) -> Media | None:
    """The next followed media whose backoff has elapsed.

    Obtained films are retired in one statement. Everything still waiting on a
    future ``next_source_poll_at`` stays out of SQL. Candidates are read in poll
    order by keyset batches (no ``OFFSET``); episodes for each batch load in one
    query. The gate still decides interval, air window, and hold.
    """
    dirty = _retire_obtained_films(db)
    interval_default = _setting_int(db, "scan_interval_min", settings.scan_interval_min)
    ongoing = _setting_bool(db, "scan_ongoing_only", settings.scan_ongoing_only)
    window = _setting_int(db, "scan_air_window_days", settings.scan_air_window_days)
    cursor: Media | None = None
    while True:
        rows = _poll_candidate_batch(db, now, cursor)
        if not rows:
            if dirty:
                _persist(db, commit=commit)
            return None
        grouped = _items_for(db, [row.id for row in rows])
        for media in rows:
            minutes = media.scan_interval_min or interval_default
            allowed, reason = _gate(
                db,
                settings,
                media,
                grouped.get(media.id, []),
                now,
                interval=timedelta(minutes=minutes),
                ongoing_only=ongoing,
                window_days=window,
            )
            if reason == "film_obtained":
                if media.next_source_poll_at is not None or media.last_poll_result != "obtained":
                    media.next_source_poll_at = None
                    media.last_poll_result = "obtained"
                    media.last_poll_message = "Obtenu."
                    dirty = True
                continue
            if reason == "before_air_date" and _defer_poll_to_release(db, settings, media):
                dirty = True
            if allowed:
                if dirty and commit:
                    _persist(db, commit=True)
                return media
        cursor = rows[-1]


def _naive_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now
    return now.astimezone(timezone.utc).replace(tzinfo=None)


def _retire_obtained_films(db: Session, *, emit_events: bool = True) -> bool:
    """Clear the next check on films that already have a file, including library matches."""
    from mediatracker.db.models import LibraryFile

    film = aliased(MediaItem)
    on_shelf = exists(
        select(LibraryFile.id).where(
            LibraryFile.media_item_id == film.id,
            LibraryFile.status == "matched",
            LibraryFile.missing_at.is_(None),
        )
    )
    rows = list(
        db.scalars(
            select(Media)
            .join(film, film.media_id == Media.id)
            .where(
                Media.followed.is_(True),
                Media.on_hold.is_(False),
                Media.new_episode_policy != "off",
                Media.type == "film",
                film.season.is_(None),
                film.episode.is_(None),
                or_(film.downloaded.is_(True), film.file_path.is_not(None), on_shelf),
                or_(
                    Media.next_source_poll_at.is_not(None),
                    Media.last_poll_result.is_(None),
                    Media.last_poll_result != "obtained",
                ),
            )
        )
        .unique()
        .all()
    )
    if not rows:
        return False
    from mediatracker.core.film_follow import stop_following_downloaded_film

    for media in rows:
        media.next_source_poll_at = None
        media.last_poll_result = "obtained"
        media.last_poll_message = "Obtenu."
        stop_following_downloaded_film(
            db,
            media,
            reason="downloaded",
            emit_event=emit_events,
        )
    return True


def _poll_candidate_filters(now: datetime) -> tuple:
    moment = _naive_utc(now)
    return (
        Media.followed.is_(True),
        Media.on_hold.is_(False),
        Media.new_episode_policy != "off",
        or_(Media.next_source_poll_at.is_(None), Media.next_source_poll_at <= moment),
        or_(
            Media.type != "film",
            Media.last_poll_result.is_(None),
            Media.last_poll_result != "obtained",
            Media.next_source_poll_at.is_not(None),
        ),
    )


def _poll_candidate_order() -> tuple:
    return (
        Media.next_source_poll_at.asc().nulls_first(),
        Media.last_source_poll_at.asc().nulls_first(),
        Media.id.asc(),
    )


def _schedule_coalesce(column):
    return func.coalesce(column, _SCHEDULE_FLOOR)


def _cursor_schedule_values(media: Media) -> tuple[datetime, datetime, int]:
    nxt = media.next_source_poll_at
    if nxt is None:
        cnxt = _SCHEDULE_FLOOR
    else:
        cnxt = _naive_utc(nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc))
    last = media.last_source_poll_at
    if last is None:
        clast = _SCHEDULE_FLOOR
    else:
        clast = _naive_utc(last if last.tzinfo else last.replace(tzinfo=timezone.utc))
    return cnxt, clast, media.id


def _poll_candidate_after(cursor: Media):
    cnxt, clast, cid = _cursor_schedule_values(cursor)
    nxt_col = _schedule_coalesce(Media.next_source_poll_at)
    last_col = _schedule_coalesce(Media.last_source_poll_at)
    return or_(
        nxt_col > cnxt,
        and_(nxt_col == cnxt, last_col > clast),
        and_(nxt_col == cnxt, last_col == clast, Media.id > cid),
    )


def _poll_candidate_batch(db: Session, now: datetime, cursor: Media | None) -> list[Media]:
    filters: list = list(_poll_candidate_filters(now))
    if cursor is not None:
        filters.append(_poll_candidate_after(cursor))
    stmt = select(Media).where(*filters).order_by(*_poll_candidate_order()).limit(_POLL_CANDIDATE_BATCH)
    return list(db.scalars(stmt).all())


def run_poll(
    db: Session,
    media: Media,
    provider_id: int,
    source: object,
    settings: Settings,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
    force: bool = False,
    use_bindings: bool = True,
    repair_reread: bool = False,
    commit: bool = True,
    single_profile=None,
    schedule_finish: bool = True,
) -> PollReport:
    moment = now or datetime.now(timezone.utc)
    draw = rng or random.Random()
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    reconciled = _reconcile_absent_files(db, media, items, settings, moment) if absence_updates_allowed(db) else False
    if _film_is_obtained(db, media, items):
        media.next_source_poll_at = None
        media.last_poll_result = "obtained"
        media.last_poll_message = "Obtenu."
        _persist(db, commit=commit)
        return PollReport(
            result="obtained",
            message="Obtenu.",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=_aware(media.last_source_poll_at) or moment,
            next_check_at=None,
        )
    if not force:
        allowed, reason = _gate(db, settings, media, items, moment)
        if not allowed:
            deferred = reason == "before_air_date" and _defer_poll_to_release(db, settings, media)
            if reconciled or deferred:
                _persist(db, commit=commit)
            return PollReport(
                result="skipped",
                message="",
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=_aware(media.last_source_poll_at) or moment,
                next_check_at=_aware(media.next_source_poll_at) or moment,
            )
    if commit:
        release_sqlite_before_network(db)
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
            draw,
            use_bindings=use_bindings,
            repair_reread=repair_reread,
            commit=commit,
            single_profile=single_profile,
            schedule_finish=schedule_finish,
        )
    except SourceFetchError as exc:
        configured = exc.code != "NOT_CONFIGURED"
        report = _finish(
            db,
            media,
            settings,
            moment,
            draw,
            result="error",
            message=scrub_url(exc.message)[:300],
            episodes=0,
            queued=0,
            proposed=0,
            failed=configured and exc.retryable,
            force=force,
            schedule=schedule_finish,
        )
        logger.warning("Source %s, média %s : %s.", getattr(source, "type", "source"), media.id, exc.code)
    _persist(db, commit=commit)
    return report


def _collect(
    db: Session,
    media: Media,
    items: list[MediaItem],
    provider_id: int,
    source: object,
    settings: Settings,
    now: datetime,
    force: bool,
    rng: random.Random,
    *,
    retry_with_search: bool = False,
    use_bindings: bool = True,
    repair_reread: bool = False,
    commit: bool = True,
    network_only: bool = False,
    single_profile=None,
    schedule_finish: bool = True,
) -> PollReport | CollectPages:
    if media.new_episode_policy == "off":
        if network_only:
            return PollReport(
                result="nothing",
                message="Politique des nouveaux épisodes : off.",
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=_aware(media.last_source_poll_at) or now,
                next_check_at=_aware(media.next_source_poll_at) or now,
            )
        return _finish(
            db,
            media,
            settings,
            now,
            rng,
            result="nothing",
            message="Politique des nouveaux épisodes : off.",
            episodes=0,
            queued=0,
            proposed=0,
            failed=False,
            force=force,
            schedule=schedule_finish,
        )
    window = _setting_int(db, "scan_air_window_days", settings.scan_air_window_days)
    backlog = bool(getattr(media, "download_backlog", False)) and not media.on_hold
    catching = backlog or (bool(media.catch_up_pending) and media.new_episode_policy == "auto" and not media.on_hold)
    slots = expected_slots(media, items, today=now.date(), window_days=window, force=force, catch_up=catching)
    if not slots:
        if network_only:
            return PollReport(
                result="nothing",
                message="Aucun épisode attendu pour le moment.",
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=_aware(media.last_source_poll_at) or now,
                next_check_at=_aware(media.next_source_poll_at) or now,
            )
        return _finish(
            db,
            media,
            settings,
            now,
            rng,
            result="nothing",
            message="Aucun épisode attendu pour le moment.",
            episodes=0,
            queued=0,
            proposed=0,
            failed=False,
            force=force,
            schedule=schedule_finish,
        )
    seasons = _seasons_for_pass(db, slots, items, now.date(), window)
    from mediatracker.core.profile_attempt_adapters import attempt_selection_criteria
    from mediatracker.core.profile_attempt_orchestration import (
        pick_remote_pages_for_profiles,
        profiles_for_poll_attempt,
    )
    from mediatracker.core.quality_profile import preferences_for

    chain, resolve_error = profiles_for_poll_attempt(db, media, settings, single_profile=single_profile)
    if resolve_error:
        message = resolve_error
        if network_only:
            return PollReport(
                result="error",
                message=message,
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=_aware(media.last_source_poll_at) or now,
                next_check_at=_aware(media.next_source_poll_at) or now,
            )
        return _finish(
            db,
            media,
            settings,
            now,
            rng,
            result="error",
            message=message,
            episodes=0,
            queued=0,
            proposed=0,
            failed=True,
            force=force,
            schedule=schedule_finish,
        )
    assert chain is not None
    spec = chain.principal
    crit = attempt_selection_criteria(spec)
    quality = crit.quality
    language = crit.language
    allow = crit.allow_fallback
    quality_fallback = list(crit.quality_fallback)
    language_fallback = list(crit.language_fallback)
    results: list = []
    pages: list = []
    pick_profile_rank = 1
    bound = pick_bound_pages(
        db,
        settings,
        media,
        provider_id,
        seasons,
        now=now,
        force_search=not use_bindings or retry_with_search,
        ignore_revalidation=repair_reread,
    )
    if bound is not None:
        report_rows = list(bound)
        pick = pick_remote_pages_for_profiles(
            report_rows,
            media=media,
            seasons=seasons,
            chain=chain,
            single_profile=single_profile,
            trust_bound_urls=True,
        )
        if pick is not None:
            pages = pick.pages
            spec = pick.profile
            pick_profile_rank = pick.profile_rank
            crit = attempt_selection_criteria(spec)
            quality = crit.quality
            language = crit.language
            allow = crit.allow_fallback
            quality_fallback = list(crit.quality_fallback)
            language_fallback = list(crit.language_fallback)
        elif report_rows:
            pages = list(report_rows)
            if media.type == "film":
                pages = pages[:1]
            else:
                pages = [row for row in pages if row.season is None or row.season in seasons][:_MAX_PAGES]
        results = report_rows
    else:
        if network_only:
            preference = _hosters(db, settings)
            return CollectPages(
                pages=[],
                report_rows=[],
                slots=slots,
                preference=preference,
                needs_search=True,
                seasons=set(season for season in seasons if season is not None),
                quality=quality,
                language=language,
                allow_fallback=allow,
                quality_fallback=quality_fallback,
                language_fallback=language_fallback,
                media_title=media.title,
                media_original_title=media.original_title,
                media_year=media.year,
                media_type=media.type,
                profile_name=spec.name,
                profile_id=getattr(spec, "quality_profile_id", None),
                profile=spec,
                from_bindings=False,
                profile_rank=pick_profile_rank,
            )
        profiles_to_try = chain.profiles
        report_rows = []
        for profile in profiles_to_try:
            attempt_crit = attempt_selection_criteria(profile)
            preferences = preferences_for(profile)
            if commit:
                release_sqlite_before_network(db)
            results = _search(
                source,
                media,
                seasons,
                attempt_crit.quality,
                attempt_crit.language,
                attempt_crit.allow_fallback,
                db,
                settings,
                preferences,
            )
            report_rows = list(results)
            pick = pick_remote_pages_for_profiles(
                report_rows,
                media=media,
                seasons=seasons,
                chain=chain,
                single_profile=profile,
            )
            if pick is None:
                continue
            pages = pick.pages
            spec = pick.profile
            pick_profile_rank = pick.profile_rank
            crit = attempt_selection_criteria(spec)
            quality = crit.quality
            language = crit.language
            allow = crit.allow_fallback
            quality_fallback = list(crit.quality_fallback)
            language_fallback = list(crit.language_fallback)
            break
    if media.type == "film":
        pages = pages[:1]
    else:
        pages = pages[:_MAX_PAGES]
    _store_report(media, report_rows, pages)
    if not pages:
        from mediatracker.core.source_select import explain_misses, no_version_message

        detail = explain_misses(
            list(results),
            title=media.title,
            original_title=media.original_title,
            year=media.year,
            media_type=media.type,
            seasons=seasons,
            quality=quality,
            language=language,
            allow_fallback=allow,
            quality_fallback=quality_fallback,
            language_fallback=language_fallback,
            profile=spec,
            tmdb_id=media.tmdb_id,
        )
        message = no_version_message(list(results), spec.name, detail)
        if network_only:
            return PollReport(
                result="nothing",
                message=message,
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=_aware(media.last_source_poll_at) or now,
                next_check_at=_aware(media.next_source_poll_at) or now,
            )
        return _finish(
            db,
            media,
            settings,
            now,
            rng,
            result="nothing",
            message=message,
            episodes=0,
            queued=0,
            proposed=0,
            failed=False,
            force=force,
            schedule=schedule_finish,
        )
    if network_only:
        preference = _hosters(db, settings)
        return CollectPages(
            pages=pages,
            report_rows=report_rows,
            slots=slots,
            preference=preference,
            seasons=set(season for season in seasons if season is not None),
            quality=quality,
            language=language,
            allow_fallback=allow,
            quality_fallback=quality_fallback,
            language_fallback=language_fallback,
            media_title=media.title,
            media_original_title=media.original_title,
            media_year=media.year,
            media_type=media.type,
            profile_name=spec.name,
            profile_id=getattr(spec, "quality_profile_id", None),
            profile=spec,
            from_bindings=bound is not None,
            profile_rank=pick_profile_rank,
        )
    if commit:
        release_sqlite_before_network(db)
    preference = _hosters(db, settings)
    found: list[LinkCandidate] = []
    for page in pages:
        try:
            found.extend(_page_links(source, page, spec, hoster_preference=preference))  # type: ignore[arg-type]
        except SourceFetchError as exc:
            _mark_binding_error(db, media.id, provider_id, page, now, exc.message)
            if bound is not None and not retry_with_search:
                return _collect(
                    db,
                    media,
                    items,
                    provider_id,
                    source,
                    settings,
                    now,
                    force,
                    rng,
                    retry_with_search=True,
                    use_bindings=use_bindings,
                    repair_reread=repair_reread,
                    commit=commit,
                    single_profile=single_profile,
                    schedule_finish=schedule_finish,
                )
            raise
        _remember_page(
            db,
            media.id,
            provider_id,
            page,
            now,
            media=media,
            items=items,
            profile_rank=pick_profile_rank,
        )
    kept = [link for link in found if hoster_allowed(link.hoster, preference)]
    kept.sort(key=lambda link: (link.season or 0, link.episode or 0, hoster_rank(link.hoster, preference), link.url))
    poll_qp_id = getattr(spec, "quality_profile_id", None) if spec is not None else None
    queued, proposed, episodes = _apply(
        db,
        media,
        items,
        slots,
        kept,
        provider_id,
        preference,
        now,
        poll_quality_profile_id=poll_qp_id,
    )
    auto = media.new_episode_policy == "auto"
    from mediatracker.core.profile_fallback import poll_profile_repli_journal_note

    message = poll_found_message(media, episodes=episodes, queued=queued, auto=auto)
    repli_note = poll_profile_repli_journal_note(spec, rank=pick_profile_rank)
    if repli_note:
        message = f"{message}{repli_note}"
    result = "nothing" if episodes == 0 else "found"
    return _finish(
        db,
        media,
        settings,
        now,
        rng,
        result=result,
        message=message,
        episodes=episodes,
        queued=queued,
        proposed=proposed,
        failed=False,
        force=force,
        schedule=schedule_finish,
    )


def _page_links(source, page, profile, *, hoster_preference=None) -> list[LinkCandidate]:
    ref = SourceRef(ref=page.ref, url=page.url)
    kwargs: dict = {}
    if hoster_preference is not None:
        kwargs["hoster_preference"] = hoster_preference
    if getattr(source, "type", None) == "wawacity" and profile is not None:
        return list(source.get_links(ref, profile=profile, **kwargs))  # type: ignore[call-arg]
    return list(source.get_links(ref, **kwargs))  # type: ignore[attr-defined]


def _search(
    source,
    media,
    seasons,
    quality,
    language,
    allow,
    db,
    settings,
    preferences=None,
):
    """Prefer a source's own ``discover``. Fall back to a plain text search."""
    discover = getattr(source, "discover", None)
    if callable(discover):
        from mediatracker.providers.base import SourcePreferences, SourceQuery

        chosen = preferences or SourcePreferences(
            quality=quality,
            language=language,
            allow_fallback=allow,
        )
        return list(
            discover(
                SourceQuery(
                    title=media.title,
                    original_title=getattr(media, "original_title", None),
                    year=media.year,
                    media_type=media.type,
                    seasons=sorted(season for season in seasons if season is not None),
                ),
                chosen,
            )
        )
    return list(source.search(media.title, media.type))  # type: ignore[attr-defined]


def _store_report(media: Media, results: list, chosen: list) -> None:
    kept = {item.url for item in chosen if getattr(item, "url", None)}
    report = []
    for result in results[:12]:
        report.append(
            {
                "title": result.title,
                "season": result.season,
                "quality": result.quality,
                "language": result.language,
                "kept": bool(result.url and result.url in kept),
                "reason": result.rejection or "",
            }
        )
    media.source_report = report


def queues_download(policy: str, backlog: bool) -> bool:
    """Auto queues every find. A one-time backlog also queues, then later episodes follow the policy."""
    return policy == "auto" or backlog


def _season_selected(media: Media, season: int | None) -> bool:
    """Null tracked seasons means all of them. An empty list downloads none."""
    if media.type != "series" or season is None:
        return True
    chosen = getattr(media, "tracked_seasons", None)
    if chosen is None:
        return True
    return season in {int(number) for number in chosen}


def _auto_gap_fill(media: Media) -> bool:
    """Followed series on auto: every past, unwatched, file-less episode."""
    if media.type != "series" or media.on_hold or media.new_episode_policy != "auto":
        return False
    followed = media.__dict__.get("followed", True)
    return followed is None or bool(followed)


def expected_slots(
    media: Media,
    items: list[MediaItem],
    *,
    today: date,
    window_days: int,
    force: bool,
    catch_up: bool = False,
) -> list[Slot]:
    """Episodes whose TMDB air date has arrived and that are not on disk yet.

    Catch-up ignores the air window and also keeps undated episodes. A followed
    series on automatic download does the same for every past air date: watched
    episodes, a future date, and an episode with no date stay out. Propose keeps
    the window. A paused series selects nothing.
    """
    if media.type == "series" and media.on_hold and not catch_up:
        return []
    if catch_up:
        return _catch_up_slots(media, items, today, force=force)
    if media.type == "film":
        release = media.next_air_date
        if release is not None and release > today and not force:
            return []
        film = next((item for item in items if item.season is None and item.episode is None), None)
        if film is not None and (film.watched or _on_disk(film)):
            return []
        return [Slot(None, None, _film_item_id(items))]
    fill = _auto_gap_fill(media)
    slots: list[Slot] = []
    for item in items:
        if (
            item.watched
            or _on_disk(item)
            or is_voluntarily_deleted(item)
            or item.season is None
            or item.episode is None
            or item.air_date is None
        ):
            continue
        if not _season_selected(media, item.season):
            continue
        if item.air_date > today:
            continue
        if not fill and not force and (today - item.air_date).days > window_days:
            continue
        slots.append(Slot(item.season, item.episode, item.id))
    if slots:
        return slots
    if (
        media.next_episode_season is not None
        and media.next_episode_number is not None
        and media.next_air_date is not None
        and media.next_air_date <= today
        and _season_selected(media, media.next_episode_season)
        and (force or (today - media.next_air_date).days <= window_days)
    ):
        return [Slot(media.next_episode_season, media.next_episode_number, None)]
    return []


def _on_disk(item: MediaItem) -> bool:
    return bool(item.downloaded or item.file_path)


def _library_has(db: Session, item: MediaItem) -> bool:
    from mediatracker.core.precheck import library_duplicate

    return library_duplicate(db, item) is not None


def _episode_air(
    db: Session,
    item: MediaItem,
    *,
    film: bool = False,
    file_missing: bool = False,
) -> EpisodeAir:
    if file_missing:
        return EpisodeAir(
            item.air_date,
            False,
            on_disk=False,
            watched=bool(item.watched),
            season=item.season,
            voluntarily_removed=is_voluntarily_deleted(item),
        )
    on_disk = bool(film and (_on_disk(item) or _library_has(db, item)))
    return EpisodeAir(
        item.air_date,
        bool(item.downloaded or on_disk),
        on_disk=on_disk,
        watched=bool(item.watched),
        season=item.season,
        voluntarily_removed=is_voluntarily_deleted(item),
    )


def _film_is_obtained(db: Session, media: Media, items: list[MediaItem]) -> bool:
    if media.type != "film":
        return False
    films = [item for item in items if item.season is None and item.episode is None]
    return any(_episode_air(db, item, film=True).on_disk or item.downloaded for item in films)


def _catch_up_slots(media: Media, items: list[MediaItem], today: date, *, force: bool = False) -> list[Slot]:
    if media.type == "film":
        release = media.next_air_date
        if release is not None and release > today and not force:
            return []
        film = next((item for item in items if item.season is None and item.episode is None), None)
        if film is not None and (film.watched or _on_disk(film)):
            return []
        return [Slot(None, None, film.id if film is not None else _film_item_id(items))]
    slots: list[Slot] = []
    for item in items:
        if item.season is None or item.episode is None:
            continue
        if not _season_selected(media, item.season):
            continue
        if item.watched or _on_disk(item) or is_voluntarily_deleted(item):
            continue
        if item.air_date is not None and item.air_date > today:
            continue
        slots.append(Slot(item.season, item.episode, item.id))
    return slots


def _seasons_for_pass(
    db: Session,
    slots: list[Slot],
    items: list[MediaItem],
    today: date,
    window_days: int,
) -> set[int]:
    """At most ``_MAX_PAGES`` seasons per check, so a long gap does not fan out.

    A season that aired inside the window goes first. Older holes follow, lowest
    number first. The next check continues with what is still missing.
    """
    seasons = _seasons_to_open(db, slots)
    if len(seasons) <= _MAX_PAGES:
        return seasons
    by_key = {(item.season, item.episode): item for item in items}
    latest: dict[int, date] = {}
    for slot in slots:
        if slot.season not in seasons:
            continue
        item = by_key.get((slot.season, slot.episode))
        air = item.air_date if item is not None else None
        if air is None:
            continue
        current = latest.get(slot.season)
        if current is None or air > current:
            latest[slot.season] = air
    recent = sorted(season for season in seasons if (today - latest.get(season, date.min)).days <= window_days)
    older = sorted(season for season in seasons if season not in set(recent))
    return set((recent + older)[:_MAX_PAGES])


def _seasons_to_open(db: Session, slots: list[Slot]) -> set[int]:
    """Seasons that still have an episode without a link. Otherwise refresh the latest one."""
    missing: set[int] = set()
    present: set[int] = set()
    for slot in slots:
        if slot.season is None:
            continue
        if slot.item_id is None or not _has_link(db, slot.item_id):
            missing.add(slot.season)
        else:
            present.add(slot.season)
    if missing:
        return missing
    if not present:
        return set()
    return {max(present)}


def _has_link(db: Session, item_id: int) -> bool:
    return db.scalar(select(Link.id).where(Link.media_item_id == item_id, Link.dead.is_(False))) is not None


def _apply(
    db: Session,
    media: Media,
    items: list[MediaItem],
    slots: list[Slot],
    links: list[LinkCandidate],
    provider_id: int,
    preference: list[str],
    now: datetime,
    *,
    poll_quality_profile_id: int | None = None,
) -> tuple[int, int, int]:
    links = [link for link in links if hoster_allowed(link.hoster, preference)]
    wanted = {(slot.season, slot.episode) for slot in slots}
    by_item = {(item.season, item.episode): item for item in items}
    grouped: dict[tuple[int | None, int | None], list[LinkCandidate]] = {}
    for link in links:
        key = (None, None) if media.type == "film" else (link.season, link.episode)
        if key not in wanted:
            continue
        bucket = grouped.setdefault(key, [])
        if any(existing.hoster == link.hoster for existing in bucket):
            continue
        bucket.append(link)
    queued = proposed = 0
    for key, bucket in grouped.items():
        if not bucket:
            continue
        item = by_item.get(key)
        if item is None:
            item = MediaItem(
                media_id=media.id,
                season=key[0],
                episode=key[1],
                state="missing",
            )
            db.add(item)
            db.flush()
            by_item[key] = item
        if item.downloaded or item.watched:
            continue
        if media.type == "film" and (item.file_path or _library_has(db, item)):
            continue
        _store_links(db, item, bucket, provider_id, preference, now)
        fresh = not item.source_available and item.state in {"missing", "available"}
        item.source_available = True
        if item.state == "missing":
            item.state = "available"
        if fresh and media.new_episode_policy == "propose":
            record_event(
                db,
                "item.available",
                media_id=media.id,
                item_id=item.id,
                dedupe_key=f"item.available:{item.id}",
                payload={
                    "title": media.title,
                    "media_id": media.id,
                    "item_id": item.id,
                    "tmdb_id": media.tmdb_id,
                    "poster_url": media.poster_url,
                    "media_type": media.type,
                    "season": item.season,
                    "episode": item.episode,
                    "episode_title": item.title,
                    "level": "info",
                },
            )
        queue = (
            not media.on_hold
            and not item.file_path
            and queues_download(media.new_episode_policy, bool(getattr(media, "download_backlog", False)))
        )
        if queue and not _blocking_download(db, item.id):
            if _requeue_failed_download(db, item.id, now, poll_quality_profile_id):
                queued += 1
            else:
                db.add(
                    Download(
                        media_item_id=item.id,
                        state="pending",
                        created_at=now,
                        poll_quality_profile_id=poll_quality_profile_id,
                    )
                )
                queued += 1
        else:
            proposed += 1
    return queued, proposed, len(grouped)


def _store_links(
    db: Session,
    item: MediaItem,
    links: list[LinkCandidate],
    provider_id: int,
    preference: list[str],
    now: datetime,
) -> None:
    for link in links:
        existing = db.scalar(select(Link).where(Link.provider_id == provider_id, Link.external_id == link.external_id))
        if existing is not None:
            if link.filename and not existing.filename:
                existing.filename = link.filename[:500]
            continue
        db.add(
            Link(
                media_item_id=item.id,
                provider_id=provider_id,
                kind=link.kind,
                url=link.url,
                hoster=link.hoster,
                quality=link.quality,
                language=link.language,
                filename=link.filename[:500] if link.filename else None,
                external_id=link.external_id,
                priority=max(0, 100 - hoster_rank(link.hoster, preference)),
                discovered_at=now,
            )
        )


_POLL_REQUEUE_FAILURE_CODES = frozenset({"NO_LINK", "PROFILE", "POLL_PROFILE_MISSING"})


def _failed_is_poll_requeue_eligible(row: Download) -> bool:
    """Échec « pas de lien » seulement (pas débrid, intégrité, DISCONTINUED, etc.)."""
    code = (row.error_code or "").strip()
    if code in _POLL_REQUEUE_FAILURE_CODES:
        return True
    bag = getattr(row, "_download_pass_verdicts", None)
    if isinstance(bag, dict) and bag:
        return all(getattr(verdict, "issue", None) in {"empty", "refused"} for verdict in bag.values())
    return False


def _requeue_failed_download(
    db: Session,
    item_id: int,
    now: datetime,
    poll_quality_profile_id: int | None,
) -> Download | None:
    """Remet en file le dernier job ``failed`` « pas de lien », si aucun job actif."""
    if active_download_for_item(db, item_id) is not None:
        return None
    row = db.scalar(
        select(Download)
        .where(Download.media_item_id == item_id, Download.state == "failed")
        .order_by(Download.id.desc())
        .limit(1)
    )
    if row is None or not _failed_is_poll_requeue_eligible(row):
        return None
    row.state = "pending"
    row.error_code = None
    row.error = None
    row.finished_at = None
    row.next_retry_at = None
    row.attempts = 0
    row.link_id = None
    row.skipped_link_ids = []
    row.poll_quality_profile_id = poll_quality_profile_id
    row.created_at = now
    return row


def _blocking_download(db: Session, item_id: int) -> bool:
    """Busy jobs block. One ``done`` does not, once the file is gone.

    A second job created for the same disappearance blocks the next one, so a
    missing file is not fetched again on every check. A later ``missing_at``
    is a new disappearance and may be tried once.
    """
    item = db.get(MediaItem, item_id)
    rows = list(db.scalars(select(Download).where(Download.media_item_id == item_id)).all())
    if active_download_for_item(db, item_id) is not None:
        return True
    failed = [row for row in rows if row.state == "failed"]
    if failed and any(not _failed_is_poll_requeue_eligible(row) for row in failed):
        return True
    dones = [row for row in rows if row.state == "done"]
    if not dones:
        return False
    if item is not None and (item.downloaded or item.file_path):
        return True
    return _replacement_already_tried(db, item, rows, dones)


def _replacement_already_tried(
    db: Session,
    item: MediaItem | None,
    rows: list[Download],
    dones: list[Download],
) -> bool:
    missing_at = _latest_missing_at(db, item.id) if item is not None and item.id is not None else None
    if missing_at is not None:
        return any(row.state != "cancelled" and _stamp(row) >= missing_at for row in rows)
    first = min(dones, key=_stamp)
    return any(row.id != first.id and row.state != "cancelled" and _stamp(row) >= _stamp(first) for row in rows)


def _latest_missing_at(db: Session, item_id: int) -> datetime | None:
    values = db.scalars(
        select(LibraryFile.missing_at).where(
            LibraryFile.media_item_id == item_id,
            LibraryFile.status == "missing",
            LibraryFile.missing_at.is_not(None),
        )
    ).all()
    stamps: list[datetime] = []
    for value in values:
        stamp = _aware(value)
        if stamp is not None:
            stamps.append(stamp)
    return max(stamps) if stamps else None


def _stamp(row: Download) -> datetime:
    return _aware(row.created_at) or datetime.min.replace(tzinfo=timezone.utc)


def _mark_binding_error(
    db: Session,
    media_id: int,
    provider_id: int,
    page,
    now: datetime,
    message: str,
) -> None:
    row = db.scalar(
        select(SourceBinding).where(
            SourceBinding.media_id == media_id,
            SourceBinding.provider_id == provider_id,
            SourceBinding.season == page.season,
            SourceBinding.active.is_(True),
        )
    )
    if row is None:
        row = db.scalar(
            select(SourceBinding).where(
                SourceBinding.media_id == media_id,
                SourceBinding.provider_id == provider_id,
                SourceBinding.external_ref == (page.ref or "")[:300],
            )
        )
    if row is None:
        return
    row.last_error = scrub_url(message)[:500]
    row.last_error_at = now


def _remember_page(
    db: Session,
    media_id: int,
    provider_id: int,
    page,
    now: datetime,
    *,
    media: Media | None = None,
    items: list | None = None,
    profile_rank: int = 1,
) -> None:
    ref_key = (page.ref or page.url or "")[:300]
    row = db.scalar(
        select(SourceBinding).where(
            SourceBinding.media_id == media_id,
            SourceBinding.provider_id == provider_id,
            SourceBinding.external_ref == ref_key,
        )
    )
    if row is None:
        row = db.scalar(
            select(SourceBinding).where(
                SourceBinding.media_id == media_id,
                SourceBinding.provider_id == provider_id,
                SourceBinding.season == page.season,
                SourceBinding.active.is_(True),
            )
        )
    if row is not None and row.path_manual:
        row.last_scanned_at = now
        row.last_error = None
        row.last_error_at = None
        return
    persist = media is None or should_persist_auto_binding(media, items, profile_rank=profile_rank)
    if not persist:
        if row is not None:
            clear_auto_binding_path(row)
        return
    if row is None:
        row = SourceBinding(
            media_id=media_id,
            provider_id=provider_id,
            external_ref=ref_key,
            url=page.url,
            quality=page.quality,
            language=page.language,
            season=page.season,
            active=True,
        )
        db.add(row)
    remember_binding_fields(row, page.url)
    row.quality = page.quality
    row.language = page.language
    row.last_scanned_at = now
    row.last_error = None
    row.last_error_at = None


def _finish(
    db: Session,
    media: Media,
    settings: Settings,
    now: datetime,
    rng: random.Random,
    *,
    result: str,
    message: str,
    episodes: int,
    queued: int,
    proposed: int,
    failed: bool,
    force: bool = False,
    schedule: bool = True,
) -> PollReport:
    if not schedule:
        return PollReport(
            result=result,
            message=message,
            episodes=episodes,
            queued=queued,
            proposed=proposed,
            last_check_at=_aware(media.last_source_poll_at) or now,
            next_check_at=_aware(media.next_source_poll_at) or now,
        )
    prev_result = media.last_poll_result
    prev_message = media.last_poll_message
    streak = media.source_error_streak or 0
    epoch = media.source_error_epoch or 0
    if failed:
        streak += 1
    else:
        if streak:
            epoch += 1
        streak = 0
    interval = _interval(db, settings, media)
    if force:
        media.poll_unchanged_streak = 0
    elif failed:
        media.poll_unchanged_streak = 0
    elif prev_result == result and (prev_message or "") == (message or ""):
        media.poll_unchanged_streak = (media.poll_unchanged_streak or 0) + 1
    else:
        media.poll_unchanged_streak = 0
    unchanged_streak = media.poll_unchanged_streak or 0
    if streak > 0:
        nxt = _next_check(now, interval, streak, _backoff_base(db, settings), rng)
    elif unchanged_streak > 0:
        multiplier = min(2**unchanged_streak, _UNCHANGED_BACKOFF_CAP_MULT)
        stretched = interval * multiplier
        span = min(max(stretched.total_seconds() * 0.1, 60), 3600)
        nxt = now + stretched + timedelta(seconds=rng.uniform(0, span))
    else:
        nxt = _next_check(now, interval, 0, _backoff_base(db, settings), rng)
    # Events flush inside a savepoint. Assigning the schedule afterwards keeps
    # a rollback of that savepoint from wiping next_source_poll_at.
    if failed and streak >= 3:
        record_event(
            db,
            "source.error",
            level="warning",
            media_id=media.id,
            dedupe_key=f"source.error:{media.id}:{epoch}",
            when=now,
            payload={
                "title": media.title,
                "media_id": media.id,
                "tmdb_id": media.tmdb_id,
                "poster_url": media.poster_url,
                "media_type": media.type,
                "level": "warning",
            },
        )
    record_event(
        db,
        "source.checked",
        level="warning" if result == "error" else "info",
        media_id=media.id,
        payload={
            "title": media.title,
            "result": result,
            "message": message,
            "episodes": episodes,
            "queued": queued,
            "proposed": proposed,
        },
        when=now,
    )
    media.source_error_streak = streak
    media.source_error_epoch = epoch
    if not failed:
        media.catch_up_pending = False
        media.download_backlog = False
    media.last_source_poll_at = now
    from mediatracker.core.fiche_check import touch_fiche_checked

    touch_fiche_checked(media, now)
    if _film_is_obtained(db, media, list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())):
        nxt = None
    media.next_source_poll_at = nxt
    media.last_poll_result = result
    media.last_poll_message = message
    if media.type == "film":
        logger.info("Contrôle source média %s : %s (film).", media.id, result)
    else:
        logger.info("Contrôle source média %s : %s (%s épisodes).", media.id, result, episodes)
    return PollReport(
        result=result,
        message=message,
        episodes=episodes,
        queued=queued,
        proposed=proposed,
        last_check_at=now,
        next_check_at=nxt,
    )


def _next_check(
    now: datetime,
    interval: timedelta,
    streak: int,
    base_minutes: int,
    rng: random.Random,
) -> datetime:
    from mediatracker.providers.sources.throttle import backoff_delay

    if streak > 0:
        delay = backoff_delay(streak, base_minutes, cap_minutes=_BACKOFF_CAP_MIN)
        jitter = rng.uniform(0, min(delay.total_seconds() * 0.1, 600))
        return now + delay + timedelta(seconds=jitter)
    span = min(max(interval.total_seconds() * 0.1, 60), 3600)
    return now + interval + timedelta(seconds=rng.uniform(0, span))


def _tracked_seasons(media: Media) -> tuple[int, ...] | None:
    chosen = getattr(media, "tracked_seasons", None)
    if chosen is None:
        return None
    return tuple(int(number) for number in chosen)


def _path_present(path: str | None) -> bool:
    """True when the recorded path is still a real video, not a sidecar or an empty file."""
    if not path:
        return False
    from mediatracker.core.library_video import valid_video

    return valid_video(Path(path))


def _missing_library_ids(db: Session, settings: Settings, items: list[MediaItem]) -> set[int]:
    """Recorded paths under the library that are no longer files.

    A path outside the library is left alone: tests and imports often store a
    placeholder, and only a real library folder can prove a hand deletion.
    """
    from mediatracker.core.storage import is_under, storage_layout

    layout = None
    roots: tuple[Path, ...] = ()
    gone: set[int] = set()
    for item in items:
        if item.id is None or not item.file_path or _path_present(item.file_path):
            continue
        if layout is None:
            layout = storage_layout(db, settings)
            roots = tuple(root for root in (layout.movies_dir, layout.series_dir) if root.exists())
        path = Path(item.file_path)
        if any(is_under(path, (root,)) for root in roots):
            gone.add(item.id)
    return gone


def refresh_library_claims(
    db: Session,
    media: Media,
    items: list[MediaItem],
    settings: Settings,
    now: datetime,
) -> bool:
    """Match a stored library path to a real video. Watched stays as it is.

    Only a path inside the film or series folder can prove a hand deletion.
    A downloaded mark with no path, and a path outside those folders, stay as
    recorded. Opening a fiche does not queue a download.
    """
    from mediatracker.core.forget import is_file_absent
    from mediatracker.core.storage import is_under, library_on_local_disk, storage_layout

    layout = storage_layout(db, settings)
    roots = tuple(root for root in (layout.movies_dir, layout.series_dir) if root.exists())
    if not roots:
        return False
    local = library_on_local_disk(db, settings)
    scoped: list[MediaItem] = []
    for item in items:
        if item.file_path and is_under(Path(item.file_path), roots):
            scoped.append(item)
        elif is_file_absent(item):
            scoped.append(item)
        elif local and item.downloaded:
            scoped.append(item)
    if not scoped:
        return False
    return _reconcile_absent_files(db, media, scoped, settings, now, local=local, fast_local=True)


def _reconcile_absent_files(
    db: Session,
    media: Media,
    items: list[MediaItem],
    settings: Settings,
    now: datetime,
    *,
    local: bool | None = None,
    fast_local: bool = False,
) -> bool:
    """Mark a hand-deleted file missing now, or keep another name already on disk.

    The daily library scan is not required. Another filename for the same
    episode stays the local copy and is not queued.
    """
    from mediatracker.core.forget import is_file_absent
    from mediatracker.core.library_video import find_episode_on_disk, find_library_video
    from mediatracker.core.storage import library_on_local_disk, library_skip, storage_layout
    from mediatracker.library.scanner import remember_filed

    layout = storage_layout(db, settings)
    skip = library_skip(layout)
    if local is None:
        local = library_on_local_disk(db, settings)
    changed = False
    for item in items:
        if media.type == "series" and (item.season is None or item.episode is None):
            continue
        if not item.downloaded and not item.file_path and not is_file_absent(item):
            continue
        if _path_present(item.file_path):
            continue
        if fast_local and local and not is_file_absent(item):
            forget_local_file(db, media, item, now)
            changed = True
            continue
        if fast_local and local:
            found = find_library_video(
                movies_dir=layout.movies_dir,
                series_dir=layout.series_dir,
                media_type=media.type,
                title=media.title,
                year=media.year,
                season=item.season,
                episode=item.episode,
                tmdb_id=media.tmdb_id,
            )
        else:
            found = find_episode_on_disk(
                movies_dir=layout.movies_dir,
                series_dir=layout.series_dir,
                media_type=media.type,
                title=media.title,
                year=media.year,
                season=item.season,
                episode=item.episode,
                tmdb_id=media.tmdb_id,
                skip=skip,
            )
        if found is not None:
            item.file_path = str(found)
            item.downloaded = True
            item.downloaded_at = item.downloaded_at or now
            if item.state in {"missing", "available", "queued", "downloading", "pending", "failed"}:
                item.state = "downloaded"
            remember_filed(db, found, media, item, now)
            changed = True
            continue
        forget_local_file(db, media, item, now)
        changed = True
    if changed:
        from mediatracker.core.fiche_check import touch_fiche_checked

        touch_fiche_checked(media, now)
    return changed


def _release_day_poll_at(db: Session, settings: Settings, air: date) -> datetime:
    """Start of ``air`` in notify timezone, stored like other poll timestamps (naive UTC)."""
    from zoneinfo import ZoneInfo

    zone_name = _setting_text(db, "notify_timezone", settings.notify_timezone)
    try:
        zone = ZoneInfo(zone_name)
    except Exception:
        zone = ZoneInfo("Europe/Paris")
    local_start = datetime.combine(air, time.min, tzinfo=zone)
    return _naive_utc(local_start.astimezone(timezone.utc))


def _defer_poll_to_release(db: Session, settings: Settings, media: Media) -> bool:
    """When polling waits for release, align next_source_poll_at to that day if still overdue."""
    air = media.next_air_date
    if air is None:
        return False
    target = _release_day_poll_at(db, settings, air)
    current = media.next_source_poll_at
    if current is not None:
        cur = _naive_utc(_aware(current))
        if cur >= target:
            return False
    media.next_source_poll_at = target
    return True


def _gate(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    now: datetime,
    *,
    interval: timedelta | None = None,
    ongoing_only: bool | None = None,
    window_days: int | None = None,
) -> tuple[bool, str]:
    if is_suspended(db):
        return False, "library_suspended"
    gone = _missing_library_ids(db, settings, items)
    subject = PollSubject(
        followed=media.followed,
        on_hold=media.on_hold,
        media_type=media.type,
        series_status=media.series_status,
        new_episode_policy=media.new_episode_policy,
        last_source_poll_at=_aware(media.last_source_poll_at),
        next_source_poll_at=_aware(media.next_source_poll_at),
        next_air_date=media.next_air_date,
        episodes=tuple(
            _episode_air(db, item, film=media.type == "film", file_missing=item.id in gone) for item in items
        ),
        catch_up=bool(media.catch_up_pending),
        backlog=bool(getattr(media, "download_backlog", False)),
        tracked_seasons=_tracked_seasons(media),
    )
    return should_poll_source(
        subject,
        now=now,
        interval=interval if interval is not None else _interval(db, settings, media),
        ongoing_only=(
            ongoing_only
            if ongoing_only is not None
            else _setting_bool(db, "scan_ongoing_only", settings.scan_ongoing_only)
        ),
        window_days=(
            window_days
            if window_days is not None
            else _setting_int(db, "scan_air_window_days", settings.scan_air_window_days)
        ),
    )


def _interval(db: Session, settings: Settings, media: Media) -> timedelta:
    minutes = media.scan_interval_min or _setting_int(db, "scan_interval_min", settings.scan_interval_min)
    return timedelta(minutes=minutes)


def _backoff_base(db: Session, settings: Settings) -> int:
    return _setting_int(db, "source_error_backoff_min", settings.source_error_backoff_min)


def _hosters(db: Session, settings: Settings) -> list[str]:
    raw = _setting_text(db, "hoster_preference", settings.hoster_preference)
    try:
        return parse_hoster_preference(raw)
    except ValueError:
        return parse_hoster_preference(",".join(DEFAULT_HOSTER_PREFERENCE))


def _items_for(db: Session, media_ids: list[int]) -> dict[int, list[MediaItem]]:
    grouped: dict[int, list[MediaItem]] = {media_id: [] for media_id in media_ids}
    if not media_ids:
        return grouped
    rows = db.scalars(select(MediaItem).where(MediaItem.media_id.in_(media_ids))).all()
    for row in rows:
        grouped.setdefault(row.media_id, []).append(row)
    return grouped


def _film_item_id(items: list[MediaItem]) -> int | None:
    for item in items:
        if item.season is None and item.episode is None:
            return item.id
    return items[0].id if len(items) == 1 else None


def _setting_text(db: Session, key: str, default: str) -> str:
    row = db.get(Setting, key)
    if row is None or row.value is None or row.value == "":
        return default
    return row.value


def _setting_int(db: Session, key: str, default: int) -> int:
    raw = _setting_text(db, key, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _setting_bool(db: Session, key: str, default: bool) -> bool:
    raw = _setting_text(db, key, "")
    if not raw:
        return default
    return raw == "true"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class SourceRereadReport:
    ok: bool
    error: str | None
    links: int
    message: str


def reread_source_for_repair(
    db: Session,
    media: Media,
    provider_id: int,
    source: object,
    settings: Settings,
    *,
    now: datetime,
) -> SourceRereadReport:
    """Profil×sources (orchestrateur) ; ``provider_id``/``source`` ignorés si orchestration active."""
    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
    from mediatracker.core.remote_search_limit import begin_remote_search, remote_search_limit_phrase
    from mediatracker.core.source_binding import bindings_need_search, seasons_needed
    from mediatracker.db.session import bind_session_factory

    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    if media.type == "film":
        seasons: set[int | None] = {None}
    else:
        seasons = {item.season for item in items if item.season is not None}
    seasons = seasons_needed(media, seasons)
    need_search = bindings_need_search(db, media, provider_id, seasons)
    use_bindings = not need_search
    if need_search and not begin_remote_search(media, now, db, settings):
        return SourceRereadReport(
            ok=False,
            error=f"Recherche source reportée ({remote_search_limit_phrase(db, settings)}).",
            links=0,
            message="",
        )
    _ = source  # conservé pour la signature ; le sondage ouvre ses propres sources.
    commit_retry(db)
    factory = bind_session_factory(db)
    report = orchestrate_profile_first_poll(
        factory,
        settings,
        media.id,
        force=True,
        now=now,
        use_bindings=use_bindings,
        repair_reread=not need_search,
    )
    if report is None:
        return SourceRereadReport(ok=False, error="Sondage impossible.", links=0, message="")
    links = db.scalar(
        select(func.count())
        .select_from(Link)
        .join(MediaItem, Link.media_item_id == MediaItem.id)
        .where(MediaItem.media_id == media.id, Link.dead.is_(False))
    )
    links = int(links or 0)
    ok = report.result not in {"error"}
    error = report.message if report.result == "error" else None
    return SourceRereadReport(ok=ok, error=error, links=links, message=report.message)


def close_source(source: object) -> None:
    close = getattr(source, "close", None)
    if close is not None:
        close()
