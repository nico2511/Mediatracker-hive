"""Download-row transitions shared by the queue and the network steps."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.naming import sanitize
from mediatracker.core.pipeline.constants import BACKOFFS, MAX_ATTEMPTS, VIDEO_EXT, french_error, logger
from mediatracker.core.precheck import PRESENT
from mediatracker.core.selection import (
    BLOCKED_SOURCE_HOSTERS,
    DEFAULT_HOSTER_PREFERENCE,
    hoster_allowed,
    hoster_rank,
    parse_hoster_preference,
)
from mediatracker.core.source_state import _source_label as _download_source_label
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider
from mediatracker.db.session import commit_retry
from mediatracker.events import record_event
from mediatracker.providers.base import AUTH_BLOCKED_MESSAGE
from mediatracker.providers.downloaders.aria2 import scrub_url
from mediatracker.settings_int import int_setting as _int_setting

_SOURCE_SWITCH_JOURNAL_COOLDOWN = timedelta(hours=1)


def _commit(db: Session) -> None:
    commit_retry(db)


def _download_sources(db: Session, media: Media, settings: Settings) -> list[str]:
    from mediatracker.core.selection import DEFAULT_DOWNLOAD_SOURCE_ORDER, parse_download_source_order

    raw = (media.source_order or "").strip() or _text_setting(
        db, "download_source_order", settings.download_source_order or DEFAULT_DOWNLOAD_SOURCE_ORDER
    )
    try:
        return parse_download_source_order(raw)
    except ValueError:
        return parse_download_source_order(DEFAULT_DOWNLOAD_SOURCE_ORDER)


def _usable_download_sources(db: Session, media: Media, settings: Settings) -> list[str]:
    from mediatracker.core.source_availability import (
        filter_enabled_download_source_order,
        filter_usable_source_order,
    )

    order = filter_enabled_download_source_order(db, settings, _download_sources(db, media, settings))
    return filter_usable_source_order(db, settings, order)


def _already_local(db: Session, item: MediaItem, now: datetime, settings: Settings) -> bool:
    """True only when a video file for this episode is still on disk."""
    from mediatracker.core.presence import local_video

    return local_video(db, settings, item, now) is not None


def _cancel_present(db: Session, row: Download, now: datetime) -> None:
    row.state = "cancelled"
    row.error_code = "ALREADY_PRESENT"
    row.error = PRESENT
    row.finished_at = now
    row.handle = None
    row.attempts = 0


def _choose_link(
    db: Session,
    row: Download,
    item: MediaItem,
    preference: list[str],
    *,
    media: Media | None = None,
    settings: Settings | None = None,
    allow_fallback: bool = False,
    qualities: list[str] | None = None,
    languages: list[str] | None = None,
    profile=None,
) -> Link | None:
    if media is None:
        media = db.get(Media, item.media_id)
    if media is None:
        return None
    attempt_profile = profile
    if attempt_profile is None:
        from mediatracker.config import get_settings
        from mediatracker.core.profile_attempt_orchestration import resolve_download_poll_profile

        resolved_settings = settings or get_settings()
        resolution = resolve_download_poll_profile(db, media, resolved_settings, row)
        if resolution.missing_explicit:
            return None
        attempt_profile = resolution.profile
    skipped = {int(value) for value in (row.skipped_link_ids or [])}
    if attempt_profile is not None:
        if row.link_id and row.attempts > 0:
            current = db.get(Link, row.link_id)
            if (
                current is not None
                and not current.dead
                and current.id not in skipped
                and _hoster_ok(current, preference)
            ):
                from mediatracker.core.profile_attempt_adapters import rank_link_for_profile_attempt

                if rank_link_for_profile_attempt(current, profile=attempt_profile) is not None:
                    return current
        links = list(db.scalars(select(Link).where(Link.media_item_id == item.id, Link.dead.is_(False))).all())
        from mediatracker.core.download_bitrate_sanity import filter_stored_links

        links = filter_stored_links(media, item, links)
        from mediatracker.core.profile_attempt_adapters import choose_link_for_profile_attempt

        return choose_link_for_profile_attempt(
            preference,
            links=links,
            profile=attempt_profile,
            skipped_link_ids=skipped,
        )
    links = list(db.scalars(select(Link).where(Link.media_item_id == item.id, Link.dead.is_(False))).all())
    from mediatracker.core.download_bitrate_sanity import filter_stored_links

    links = filter_stored_links(media, item, links)
    links = [
        link
        for link in links
        if link.id not in skipped and link.kind not in {"magnet", "torrent"} and _hoster_ok(link, preference)
    ]
    ranked: list[tuple[int, Link]] = []
    for link in links:
        penalty = _version_penalty(link, media, allow_fallback, qualities or [], languages or [], profile=profile)
        if penalty is None:
            continue
        ranked.append((penalty, link))
    if row.link_id and row.attempts > 0:
        current = db.get(Link, row.link_id)
        if current is not None and not current.dead and current.id not in skipped and _hoster_ok(current, preference):
            return current
    if not ranked:
        return None
    ranked.sort(key=lambda pair: (pair[0], hoster_rank(pair[1].hoster, preference), -pair[1].priority, pair[1].id))
    return ranked[0][1]


def _hoster_ok(link: Link, preference: list[str]) -> bool:
    """Blocked hosters stay out. A named hoster must be in the configured order.

    An empty preference means « is any link left », used after a skip. A link
    with no hoster name (some protected URLs) is kept.
    """
    name = (link.hoster or "").casefold()
    if name in BLOCKED_SOURCE_HOSTERS:
        return False
    if not name or not preference:
        return True
    return hoster_allowed(name, preference)


def _version_penalty(
    link: Link,
    media: Media | None,
    allow: bool,
    qualities: list[str],
    languages: list[str],
    profile=None,
) -> int | None:
    """0 is the fiche's quality and language. Fallback versions come after, when allowed."""
    if profile is not None and link.quality and link.language:
        from mediatracker.core.profile_attempt_adapters import rank_link_for_profile_attempt

        return rank_link_for_profile_attempt(link, profile=profile)
    if media is None or not link.quality or not link.language:
        return 0
    if link.quality == media.pref_quality and link.language == media.pref_language:
        return 0
    if not allow:
        return None
    from mediatracker.core.source_select import variant_rank
    from mediatracker.providers.base import SourceResult

    return variant_rank(
        SourceResult(title=media.title, ref="link", quality=link.quality, language=link.language),
        quality=media.pref_quality,
        language=media.pref_language,
        allow_fallback=True,
        quality_fallback=qualities,
        language_fallback=languages,
    )


def _bury(db: Session, row: Download, link: Link, code: str, message: str) -> None:
    """Drop this hoster and keep looking. The job stays out of the pending list."""
    from datetime import datetime, timezone

    from mediatracker.core.dead_links import mark_link_dead

    hoster = link.hoster
    mark_link_dead(link, code, french_error(code, message), datetime.now(timezone.utc))
    _remember_skip(row, link.id)
    row.link_id = None
    row.attempts = 0
    row.attempt_started_at = None
    row.error_code = code
    row.error = link.last_error
    item = db.get(MediaItem, row.media_item_id)
    media = db.get(Media, item.media_id) if item is not None else None
    record_event(
        db,
        "download.hoster_switched",
        media_id=media.id if media is not None else None,
        item_id=row.media_item_id,
        payload={
            "download_id": row.id,
            "from_hoster": hoster,
            "code": code,
            "message": link.last_error,
            "title": media.title if media is not None else None,
            "season": item.season if item is not None else None,
            "episode": item.episode if item is not None else None,
        },
    )


def _source_switch_journal_dedupe_key(row: Download, from_source: str, to_source: str, *, now: datetime) -> str:
    hour = int(now.timestamp() // int(_SOURCE_SWITCH_JOURNAL_COOLDOWN.total_seconds()))
    return f"download.source_switched:{row.id}:{row.state}:{from_source}:{to_source}:{hour}"


def _download_profile_repli_journal_message(profile, *, rank: int) -> str:
    from mediatracker.core.quality_profile import Profile

    name = profile.name if isinstance(profile, Profile) else str(profile)
    return f"Passage au profil de repli « {name} » (rang {rank})."


def _record_download_profile_repli_switch(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    profile,
    *,
    rank: int,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    message = _download_profile_repli_journal_message(profile, rank=rank)
    record_event(
        db,
        "download.profile_repli",
        media_id=media.id,
        item_id=item.id,
        payload={
            "download_id": row.id,
            "profile_id": getattr(profile, "quality_profile_id", None),
            "profile_name": getattr(profile, "name", None),
            "rank": rank,
            "message": message,
            "title": media.title,
            "season": item.season,
            "episode": item.episode,
        },
        when=moment,
    )


def peek_next_download_profile_repli(
    db: Session,
    media: Media,
    settings: Settings,
    download: Download,
):
    from mediatracker.core.profile_attempt_orchestration import (
        download_attempt_profile,
        next_profile_repli_in_chain,
    )

    current = download_attempt_profile(db, media, settings, download)
    if current is None:
        return None
    return next_profile_repli_in_chain(db, media, settings, current)


def advance_download_profile_repli(
    db: Session,
    media: Media,
    item: MediaItem,
    row: Download,
    settings: Settings,
    *,
    now: datetime,
) -> bool:
    nxt = peek_next_download_profile_repli(db, media, settings, row)
    if nxt is None:
        return False
    row.poll_quality_profile_id = nxt.profile_id
    _record_download_profile_repli_switch(db, row, media, item, nxt.profile, rank=nxt.rank, now=now)
    return True


def _record_download_source_switch(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    from_source: str,
    to_source: str,
    *,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    readable_from = _download_source_label(from_source)
    readable_to = _download_source_label(to_source)
    record_event(
        db,
        "download.source_switched",
        media_id=media.id,
        item_id=item.id,
        payload={
            "download_id": row.id,
            "from_source": from_source,
            "to_source": to_source,
            "from_label": readable_from,
            "to_label": readable_to,
            "message": f"Bascule de source : {readable_from} vers {readable_to}",
            "title": media.title,
            "season": item.season,
            "episode": item.episode,
        },
        dedupe_key=_source_switch_journal_dedupe_key(row, from_source, to_source, now=moment),
        when=moment,
    )


def _choice_list(db: Session, key: str, default: str) -> list[str]:
    from mediatracker.core.selection import LANGUAGE_OPTIONS, QUALITY_OPTIONS, parse_choice_list

    options = QUALITY_OPTIONS if "quality" in key else LANGUAGE_OPTIONS
    try:
        return parse_choice_list(_text_setting(db, key, default), options)
    except ValueError:
        return parse_choice_list(default, options)


def _hoster_supported(debrid, link: Link) -> bool:
    supports = getattr(debrid, "supports", None)
    if supports is None or not link.hoster:
        return True
    from mediatracker.providers.base import LinkCandidate

    try:
        allowed = {"ddl_protected", "ddl_hoster", "direct", "magnet", "torrent"}
        kind = link.kind if link.kind in allowed else "ddl_hoster"
        return bool(
            supports(
                LinkCandidate(
                    url=link.url,
                    kind=kind,
                    hoster=link.hoster,
                    external_id=str(link.id),
                )
            )
        )
    except Exception:
        return True


def _rediscover(
    db: Session,
    settings: Settings,
    media: Media,
    now: datetime,
    *,
    download: Download | None = None,
    search_slot_reserved: bool = False,
) -> None:
    """Profil×sources orchestré (force) ; libère la session appelante avant le réseau."""
    from mediatracker.core.profile_attempt_orchestration import (
        POLL_PROFILE_MISSING,
        orchestrate_profile_first_poll,
        resolve_download_poll_profile,
    )
    from mediatracker.core.remote_search_limit import begin_remote_search, remote_search_limit_phrase
    from mediatracker.db.session import bind_session_factory

    restrict_profile = None
    attach_download_id = None
    attach_item_id = None
    if download is not None:
        resolution = resolve_download_poll_profile(db, media, settings, download)
        if resolution.missing_explicit:
            download.error_code = POLL_PROFILE_MISSING
            download.error = french_error(POLL_PROFILE_MISSING, "Profil du sondage introuvable.")
            _commit(db)
            return
        if resolution.explicit and resolution.profile is not None:
            restrict_profile = resolution.profile
        elif download.poll_quality_profile_id is None:
            attach_download_id = download.id
            attach_item_id = download.media_item_id

    if not search_slot_reserved and not begin_remote_search(media, now, db, settings):
        logger.info(
            "Recherche source ignorée pour le média %s (%s).",
            media.id,
            remote_search_limit_phrase(db, settings),
        )
        return
    _commit(db)
    factory = bind_session_factory(db)
    orchestrate_profile_first_poll(
        factory,
        settings,
        media.id,
        force=True,
        now=now,
        use_bindings=False,
        restrict_single_profile=restrict_profile,
        attach_profile_download_id=attach_download_id,
        attach_profile_item_id=attach_item_id,
    )
    if download is not None:
        db.refresh(download)


def _ingest_other_versions(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    now: datetime,
    *,
    attempt_profile=None,
    from_next_repli: bool = False,
) -> bool:
    """Réingest sur l'ordre utilisateur des sources, en progressant dans la chaîne profil."""
    from mediatracker.core.profile_attempt_adapters import attempt_selection_criteria, reingest_select_pages
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain, profile_chain_from_anchor
    from mediatracker.core.profile_fallback import ProfileResolveError
    from mediatracker.core.quality_profile import Profile, preferences_for
    from mediatracker.core.remote_search_limit import begin_remote_search, remote_search_limit_phrase
    from mediatracker.core.source_poll import _search, _store_links, close_source
    from mediatracker.providers.sources.loading import remote_links_source
    from mediatracker.providers.sources.registry import for_type

    if attempt_profile is not None:
        chain = profile_chain_from_anchor(db, media, settings, attempt_profile)
        specs: tuple[Profile, ...] = chain.profiles[1:] if from_next_repli else chain.profiles
    else:
        try:
            specs = (load_profile_chain(db, media, settings).principal,)
        except ProfileResolveError:
            return False
    if not specs:
        return False
    if not begin_remote_search(media, now, db, settings):
        logger.info(
            "Repli de version ignoré pour le média %s (%s).",
            media.id,
            remote_search_limit_phrase(db, settings),
        )
        return False
    dead = {
        (link.quality, link.language)
        for link in db.scalars(select(Link).where(Link.media_item_id == item.id, Link.dead.is_(True))).all()
    }
    seasons = {item.season} if item.season is not None else set()
    preference = _hosters(db, settings)
    order = _usable_download_sources(db, media, settings)
    item_id = item.id
    _commit(db)
    from mediatracker.db.session import apply_with_retry, bind_session_factory

    factory = bind_session_factory(db)
    for spec in specs:
        crit = attempt_selection_criteria(spec)
        preferences = preferences_for(spec)
        for type_name in order:
            entry = for_type(type_name)
            if entry is None or "links" not in getattr(entry, "capabilities", frozenset()):
                continue
            opened = remote_links_source(db, settings, type_name)
            if opened is None:
                continue
            provider_id, source = opened
            cache = getattr(source, "cache", None)
            if cache is not None and hasattr(cache, "begin_bypass"):
                cache.begin_bypass()
            payload: dict | None = None
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
                    preferences,
                )
                pages = reingest_select_pages(
                    list(results),
                    media=media,
                    seasons=seasons or {0},
                    profile=spec,
                    exclude=dead,
                )
                if not pages:
                    continue
                page = pages[0]
                if not page.url:
                    continue
                from mediatracker.core.source_poll import _page_links

                found = list(_page_links(source, page, spec))
                kept = [link for link in found if hoster_allowed(link.hoster, preference)]
                if item.season is not None:
                    kept = [link for link in kept if link.season == item.season and link.episode == item.episode]
                if not kept:
                    continue
                payload = {
                    "kept": kept,
                    "provider_id": provider_id,
                    "preference": preference,
                    "now": now,
                }
            except Exception:
                logger.info("Repli de version ignoré sur %s pour le média %s.", type_name, media.id)
            finally:
                if cache is not None and hasattr(cache, "end_bypass"):
                    cache.end_bypass()
                close_source(source)
            if payload is None:
                continue

            def _apply(db: Session, body: dict = payload):
                row_item = db.get(MediaItem, item_id)
                if row_item is None:
                    return False
                before = db.scalar(select(Link.id).where(Link.media_item_id == item_id, Link.dead.is_(False)))
                _store_links(
                    db,
                    row_item,
                    body["kept"],
                    body["provider_id"],
                    body["preference"],
                    body["now"],
                )
                db.flush()
                after = db.scalar(select(Link.id).where(Link.media_item_id == item_id, Link.dead.is_(False)))
                return before is None and after is not None

            if apply_with_retry(factory, f"Réingest {type_name}", _apply):
                return True
    return False


def _throttle_poll(db: Session, media: Media, settings: Settings, now: datetime) -> None:
    minutes = _int_setting(db, "source_error_backoff_min", settings.source_error_backoff_min)
    media.next_source_poll_at = now + timedelta(minutes=max(minutes, 30))


def _fast_source_poll(db: Session, media: Media, settings: Settings, now: datetime) -> None:
    """Recherche Wawacity dès que possible sans consommer le quota remote_search_limit."""
    media.next_source_poll_at = now
    from mediatracker.core.worker_wake import REASON_API_QUEUE, wake_worker

    wake_worker(db, settings, REASON_API_QUEUE, commit=False)


def _schedule_retry(
    db: Session, row: Download, code: str, message: str, now: datetime, *, defer_final: bool = False
) -> str:
    if row.state == "failed":
        return "failed"
    row.attempts += 1
    row.error_code = code
    row.error = scrub_url(message)[:500]
    row.handle = None
    row.attempt_started_at = None
    row.state = "pending"
    if row.attempts >= MAX_ATTEMPTS:
        link = db.get(Link, row.link_id) if row.link_id else None
        if link is not None:
            _bury(db, row, link, code, message)
        else:
            row.attempts = 0
            row.link_id = None
        item = db.get(MediaItem, row.media_item_id)
        if item is None or (_choose_link(db, row, item, []) is None and _no_fallback(db, row)):
            if defer_final:
                return "exhausted"
            _fail(db, row, code, message, now)
            return "failed"
        row.next_retry_at = now
        return "waiting"
    row.next_retry_at = now + BACKOFFS[row.attempts - 1]
    return "waiting"


def _no_fallback(db: Session, row: Download) -> bool:
    item = db.get(MediaItem, row.media_item_id)
    if item is None:
        return True
    return _choose_link(db, row, item, []) is None


def _skip_link(db, row: Download, link: Link, code: str, message: str, now: datetime, *, dead: bool) -> None:
    from mediatracker.core.dead_links import clear_dead_state, mark_link_dead

    if dead:
        mark_link_dead(link, code, scrub_url(message), now)
    else:
        clear_dead_state(link)
    _remember_skip(row, link.id)
    row.link_id = None
    row.attempts = 0
    row.state = "pending"
    row.error_code = code
    row.error = link.last_error
    row.next_retry_at = now
    if _no_fallback(db, row):
        _fail(db, row, code, message, now)


def _remember_skip(row: Download, link_id: int) -> None:
    skipped = [int(value) for value in (row.skipped_link_ids or [])]
    if link_id not in skipped:
        skipped.append(link_id)
    row.skipped_link_ids = skipped


def _fail(db: Session, row: Download, code: str, message: str, now: datetime) -> None:
    row.state = "failed"
    row.error_code = code
    row.error = scrub_url(message)[:500]
    row.finished_at = now
    row.handle = None
    row.next_retry_at = None
    row.attempt_started_at = None
    item = db.get(MediaItem, row.media_item_id)
    media = db.get(Media, item.media_id) if item is not None else None
    record_event(
        db,
        "download.failed",
        level="warning",
        media_id=media.id if media is not None else None,
        item_id=row.media_item_id,
        dedupe_key=f"download.failed:{row.id}",
        payload={
            "download_id": row.id,
            "error_code": code,
            "error": row.error,
            "title": media.title if media is not None else None,
            "media_id": media.id if media is not None else None,
            "tmdb_id": media.tmdb_id if media is not None else None,
            "poster_url": media.poster_url if media is not None else None,
            "media_type": media.type if media is not None else None,
            "season": item.season if item is not None else None,
            "episode": item.episode if item is not None else None,
            "item_id": item.id if item is not None else None,
            "level": "warning",
            "status": "En erreur",
        },
    )
    if item is not None and media is not None:
        from mediatracker.config import get_settings
        from mediatracker.core.profile_replacement import on_download_terminal

        on_download_terminal(
            db,
            get_settings(),
            media,
            item,
            row,
            success=False,
            new_dest=None,
            now=now,
        )


def _block_queue(db: Session, row: Provider | None, now: datetime) -> None:
    if row is None:
        return
    changed = row.health_code != "AUTH_BLOCKED"
    row.health_status = "down"
    row.health_code = "AUTH_BLOCKED"
    row.health_message = AUTH_BLOCKED_MESSAGE
    row.health_checked_at = now
    if changed:
        record_event(
            db,
            "provider.health_changed",
            level="warning",
            payload={
                "provider_id": row.id,
                "type": row.type,
                "status": "down",
                "code": "AUTH_BLOCKED",
                "message": AUTH_BLOCKED_MESSAGE,
            },
        )
        record_event(
            db,
            "debrid.auth_blocked",
            level="warning",
            dedupe_key=f"debrid.auth_blocked:{row.id}:{int(now.timestamp())}",
            when=now,
            payload={
                "title": "AllDebrid",
                "message": AUTH_BLOCKED_MESSAGE,
                "provider_id": row.id,
                "code": "AUTH_BLOCKED",
                "level": "warning",
            },
        )


def _item_state(db: Session, row: Download, state: str) -> None:
    item = db.get(MediaItem, row.media_item_id)
    if item is not None and item.state not in {"downloaded", "watched"}:
        item.state = state


def _output_name(filename: str | None, link: Link | None, item: MediaItem, media: Media) -> str:
    source = filename or (link.filename if link is not None else None) or "video.mkv"
    suffix = Path(source).suffix.lower()
    if suffix not in VIDEO_EXT:
        suffix = ".mkv"
    if media.type == "series" and item.season is not None and item.episode is not None:
        stem = f"{sanitize(media.title)} - S{item.season:02d}E{item.episode:02d}"
    else:
        stem = sanitize(Path(source).stem) or sanitize(media.title)
    return f"{stem}{suffix}"


def hoster_preference_list(db: Session, settings: Settings) -> list[str]:
    raw = _text_setting(db, "hoster_preference", settings.hoster_preference)
    try:
        return parse_hoster_preference(raw)
    except ValueError:
        return parse_hoster_preference(",".join(DEFAULT_HOSTER_PREFERENCE))


_hosters = hoster_preference_list


def _text_setting(db: Session, key: str, default: str) -> str:
    from mediatracker.db.models import Setting

    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    return row.value


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
