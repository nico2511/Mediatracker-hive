"""Repli scan : sources ``episode`` (Comet, Torrentio, addons) quand le sondage ``links`` n'a rien retenu."""

from __future__ import annotations

import logging
import random
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.core.pipeline.download_guard import active_download_for_item
from mediatracker.core.profile_attempt_orchestration import ProfileChain
from mediatracker.core.profile_fallback import poll_profile_repli_journal_note
from mediatracker.core.remote_search_limit import (
    begin_remote_search,
    remote_search_limit_phrase,
    remote_search_reserved_same_pass,
)
from mediatracker.core.source_poll import (
    PollReport,
    Slot,
    _blocking_download,
    _library_has,
    _requeue_failed_download,
    _setting_bool,
    _setting_int,
    expected_slots,
    poll_found_message,
    queues_download,
)
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.db.session import apply_with_retry, commit_retry, release_sqlite_before_network
from mediatracker.providers.base import EpisodeQuery, SourceFetchError
from mediatracker.providers.sources.registry import for_type

logger = logging.getLogger(__name__)

_HOSTER_LINK_KINDS = frozenset({"ddl_hoster", "ddl_protected", "direct"})
SCAN_EPISODE_SOURCES_FALLBACK_KEY = "scan_episode_sources_fallback"


def scan_episode_sources_fallback_enabled(db: Session, settings: Settings) -> bool:
    default = bool(getattr(settings, "scan_episode_sources_fallback", True))
    return _setting_bool(db, SCAN_EPISODE_SOURCES_FALLBACK_KEY, default)


def links_poll_retained_page(report: PollReport | None) -> bool:
    """True when a ``links`` source actually opened at least one page this pass."""
    if report is None:
        return False
    if report.queued > 0 or report.episodes > 0:
        return True
    if report.result in {"found", "obtained"}:
        return True
    message = report.message or ""
    return "ne contient pas" in message


def _episode_sources_in_order(db: Session, media: Media, settings: Settings) -> tuple[str, ...]:
    from mediatracker.core.pipeline.jobs import _usable_download_sources

    order = _usable_download_sources(db, media, settings)
    picked: list[str] = []
    for name in order:
        entry = for_type(name)
        if entry is None:
            continue
        if "episode" not in getattr(entry, "capabilities", frozenset()):
            continue
        picked.append(name)
    return tuple(picked)


def _item_for_slot(db: Session, media: Media, items: list[MediaItem], slot: Slot) -> MediaItem | None:
    if slot.item_id is not None:
        return db.get(MediaItem, int(slot.item_id))
    key = (slot.season, slot.episode)
    for item in items:
        if (item.season, item.episode) == key:
            return item
    if media.type == "film":
        return next((item for item in items if item.season is None and item.episode is None), None)
    return None


def _should_probe_item(db: Session, media: Media, item: MediaItem) -> bool:
    """Whether remote episode sources should be queried (independent of queue policy)."""
    if item.downloaded or item.watched:
        return False
    if media.type == "film" and (item.file_path or _library_has(db, item)):
        return False
    if media.on_hold:
        return False
    return True


def _should_queue_item(db: Session, media: Media, item: MediaItem) -> bool:
    if item.downloaded or item.watched:
        return False
    if media.type == "film" and (item.file_path or _library_has(db, item)):
        return False
    if active_download_for_item(db, item.id) is not None:
        return False
    if _blocking_download(db, item.id):
        return False
    if not queues_download(media.new_episode_policy, bool(getattr(media, "download_backlog", False))):
        return False
    if media.on_hold:
        return False
    return True


def _ensure_item_for_slot(db: Session, media: Media, items: list[MediaItem], slot: Slot) -> MediaItem | None:
    item = _item_for_slot(db, media, items, slot)
    if item is not None:
        return item
    if slot.season is None or slot.episode is None:
        return None
    item = MediaItem(
        media_id=media.id,
        season=slot.season,
        episode=slot.episode,
        state="missing",
    )
    db.add(item)
    db.flush()
    items.append(item)
    return item


def _magnets_cached_in_alldebrid(debrid, magnet_urls: list[str]) -> set[str]:
    from mediatracker.core.magnet_claim import _btih
    from mediatracker.providers.debrid.alldebrid import AllDebridError

    if not magnet_urls or debrid is None or not hasattr(debrid, "magnet_cache"):
        return set()
    try:
        hits = debrid.magnet_cache(magnet_urls)
    except AllDebridError:
        return set()
    cached_hashes: set[str] = set()
    for hit in hits:
        if not getattr(hit, "cached", False):
            continue
        digest = _btih(getattr(hit, "magnet", "") or "")
        if digest:
            cached_hashes.add(digest)
    kept: set[str] = set()
    for url in magnet_urls:
        if _btih(url) in cached_hashes:
            kept.add(url)
    return kept


def _pick_episode_candidate(candidates, *, debrid, preference: list[str]):
    from mediatracker.core.selection import hoster_allowed

    magnets = [item for item in candidates if getattr(item, "kind", None) == "magnet"]
    cached_urls = _magnets_cached_in_alldebrid(debrid, [item.url for item in magnets if item.url])
    for item in magnets:
        if item.url and item.url in cached_urls:
            return item
    for item in candidates:
        if getattr(item, "kind", None) in _HOSTER_LINK_KINDS and hoster_allowed(item.hoster, preference):
            return item
    return None


def _fetch_episode_candidates(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    source_type: str,
    profile,
    preferences,
    *,
    imdb: str,
) -> list:
    from mediatracker.core.pipeline.unlock import _stored_release_names
    from mediatracker.providers.sources.comet_urls import resolve_comet_base_urls
    from mediatracker.providers.sources.loading import episode_source, source_base_url

    comet_urls: list[str] | None = None
    stremio_addons = None
    if source_type == "comet":
        comet_urls = resolve_comet_base_urls(db, settings)
        if not comet_urls:
            return []
    elif source_type == "stremio_addons":
        from mediatracker.providers.sources.stremio_addons_config import enabled_stremio_addons

        stremio_addons = enabled_stremio_addons(db, settings)
        if not stremio_addons:
            return []
    else:
        base = source_base_url(db, settings, source_type).strip()
        if not base:
            return []
    if comet_urls is not None:
        source = episode_source(source_type, settings, db=db, base_urls=comet_urls)
    elif stremio_addons is not None:
        source = episode_source(source_type, settings, db=db, addons=stremio_addons)
    else:
        base = source_base_url(db, settings, source_type).strip()
        source = episode_source(source_type, settings, db=db, base_url=base)
    if source is None:
        return []
    source.quality_profile = profile
    query = EpisodeQuery(
        title=media.title,
        original_title=media.original_title,
        year=media.year,
        media_type=media.type if media.type in {"film", "series"} else "film",
        imdb_id=imdb,
        season=item.season,
        episode=item.episode,
        release_names=_stored_release_names(db, item),
    )
    release_sqlite_before_network(db)
    try:
        candidates = list(source.links_for(query, preferences))
        if source_type == "torrentio":
            from mediatracker.core.pipeline.unlock import _comet_info_hashes_on_item
            from mediatracker.providers.sources.torrentio import drop_hashes_seen_in_comet

            candidates = drop_hashes_seen_in_comet(candidates, _comet_info_hashes_on_item(db, item))
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            close()
    from mediatracker.core.download_bitrate_sanity import filter_link_candidates

    return filter_link_candidates(media, item, candidates)


def maybe_scan_episode_sources_fallback(
    factory: sessionmaker,
    settings: Settings,
    media_id: int,
    chain: ProfileChain,
    *,
    moment: datetime,
    force: bool,
    rng: random.Random,
    links_retained_any_page: bool,
    chosen: PollReport | None,
) -> PollReport | None:
    """Network + apply ; ``None`` si le repli ne s'applique pas ou n'a rien trouvé."""
    if chosen is not None or links_retained_any_page:
        return None
    read = factory()
    try:
        media = read.get(Media, media_id)
        if media is None or not media.followed or media.new_episode_policy == "off":
            return None
        if not scan_episode_sources_fallback_enabled(read, settings):
            return None
        slot_reused = remote_search_reserved_same_pass(media, moment)
        if not slot_reused and not begin_remote_search(media, moment, read, settings):
            logger.info(
                "Repli sources épisode ignoré pour le média %s (%s).",
                media.id,
                remote_search_limit_phrase(read, settings),
            )
            commit_retry(read)
            return None
        items = list(read.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        window = _setting_int(read, "scan_air_window_days", settings.scan_air_window_days)
        backlog = bool(getattr(media, "download_backlog", False)) and not media.on_hold
        catching = backlog or (
            bool(media.catch_up_pending) and media.new_episode_policy == "auto" and not media.on_hold
        )
        slots = expected_slots(media, items, today=moment.date(), window_days=window, force=force, catch_up=catching)
        if not slots:
            commit_retry(read)
            return None
        episode_order = _episode_sources_in_order(read, media, settings)
        if not episode_order:
            commit_retry(read)
            return None
        from mediatracker.core.tmdb import ensure_imdb

        imdb = ensure_imdb(read, media, settings)
        if not imdb:
            commit_retry(read)
            return None
        commit_retry(read)
    finally:
        read.close()

    from mediatracker.core.pipeline.clients import _debrid
    from mediatracker.core.pipeline.jobs import _hosters
    from mediatracker.core.pipeline.unlock import _source_unavailable_reason, _store_source_link
    from mediatracker.core.profile_attempt_adapters import comet_context_for_attempt
    from mediatracker.core.source_availability import source_usable

    prep = factory()
    try:
        _, debrid = _debrid(prep, settings, None)
        preference = _hosters(prep, settings)
    finally:
        prep.close()

    wins: list[tuple[int | None, int | None, str, object, int, int]] = []
    for slot in slots:
        plan = factory()
        try:
            media = plan.get(Media, media_id)
            if media is None:
                continue
            items = list(plan.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
            item = _item_for_slot(plan, media, items, slot)
            if item is None:
                if slot.season is not None and slot.episode is not None:
                    item = MediaItem(
                        media_id=media.id,
                        season=slot.season,
                        episode=slot.episode,
                        state="missing",
                    )
                else:
                    continue
            if not _should_probe_item(plan, media, item):
                continue
            slot_done = False
            for profile in chain.profiles:
                if profile.quality_profile_id is None:
                    continue
                spec, preferences = comet_context_for_attempt(profile=profile)
                for source_type in episode_order:
                    if not source_usable(plan, settings, source_type):
                        continue
                    reason = _source_unavailable_reason(plan, settings, source_type)
                    if reason:
                        continue
                    try:
                        candidates = _fetch_episode_candidates(
                            plan,
                            settings,
                            media,
                            item,
                            source_type,
                            spec,
                            preferences,
                            imdb=imdb,
                        )
                    except SourceFetchError as exc:
                        logger.info(
                            "Repli scan %s : %s (média %s).",
                            source_type,
                            exc.message or exc.code,
                            media_id,
                        )
                        continue
                    except Exception:
                        logger.exception("Repli scan %s en erreur (média %s).", source_type, media_id)
                        continue
                    if not candidates:
                        continue
                    choice = _pick_episode_candidate(candidates, debrid=debrid, preference=preference)
                    if choice is None:
                        continue
                    logger.info(
                        "Repli scan : %s a retenu un flux pour le média %s (S%sE%s).",
                        source_type,
                        media_id,
                        item.season if item.season is not None else "-",
                        item.episode if item.episode is not None else "-",
                    )
                    profile_rank = chain.profiles.index(profile) + 1
                    wins.append(
                        (
                            slot.season,
                            slot.episode,
                            source_type,
                            choice,
                            int(profile.quality_profile_id),
                            profile_rank,
                        )
                    )
                    slot_done = True
                    break
                if slot_done:
                    break
        finally:
            plan.close()

    if not wins:
        return None

    def _apply(db: Session):
        row = db.get(Media, media_id)
        if row is None:
            return None
        queued = proposed = 0
        episodes_touched: set[tuple[int | None, int | None]] = set()
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
        for season, episode, source_type, choice, profile_id, _profile_rank in wins:
            slot = Slot(season, episode, None)
            item = _ensure_item_for_slot(db, row, items, slot)
            if item is None:
                continue
            _store_source_link(db, item, choice, moment, source_type)
            item.source_available = True
            if item.state == "missing":
                item.state = "available"
            episodes_touched.add((item.season, item.episode))
            poll_qp_id = profile_id
            if _should_queue_item(db, row, item):
                if _requeue_failed_download(db, item.id, moment, poll_qp_id):
                    queued += 1
                else:
                    db.add(
                        Download(
                            media_item_id=item.id,
                            state="pending",
                            created_at=moment,
                            poll_quality_profile_id=poll_qp_id,
                        )
                    )
                    queued += 1
            else:
                proposed += 1
        auto = row.new_episode_policy == "auto"
        message = poll_found_message(row, episodes=len(episodes_touched), queued=queued, auto=auto)
        _season, _episode, source_type, _choice, profile_id, profile_rank = wins[0]
        first_profile = next((p for p in chain.profiles if p.quality_profile_id == profile_id), chain.profiles[0])
        repli = poll_profile_repli_journal_note(first_profile, rank=profile_rank)
        if repli:
            message = f"{message}{repli}"
        message = f"{message} (repli {source_type})."
        return PollReport(
            result="found" if episodes_touched else "nothing",
            message=message,
            episodes=len(episodes_touched),
            queued=queued,
            proposed=proposed,
            last_check_at=row.last_source_poll_at or moment,
            next_check_at=row.next_source_poll_at,
        )

    return apply_with_retry(factory, "Repli scan sources épisode", _apply)