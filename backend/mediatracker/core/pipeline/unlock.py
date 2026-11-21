"""Hoster unlock, Comet magnets, and the direct-link probe. Network calls happen after commit."""

import base64
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.link_hoster_precheck import (
    HosterUnlockContext,
    apply_hoster_inspection_to_link,
    profile_rank_for_attempt,
)
from mediatracker.core.pipeline.constants import (
    _BTIH,
    _DEAD_LINK,
    _FRENCH,
    _HTML_MAGIC,
    _HTML_TYPES,
    _MAGNET_SOURCE_HASH,
    _PERMANENT_MAGNET,
    _RELEASE_HINT,
    COMET_BACKOFFS,
    COMET_PER_CYCLE,
    french_error,
    host_down,
    logger,
)
from mediatracker.core.pipeline.download_pass_errors import record_download_pass_verdict
from mediatracker.core.pipeline.jobs import (
    _aware,
    _block_queue,
    _bury,
    _cancel_present,
    _commit,
    _fail,
    _hoster_supported,
    _ingest_other_versions,
    _remember_skip,
    _schedule_retry,
    _throttle_poll,
)
from mediatracker.core.pipeline.source_attempt_journal import (
    format_source_attempt_message,
    record_download_source_attempt,
    record_manual_link_failed,
    record_manual_link_used,
    record_manual_source_page_empty,
    record_manual_source_page_used,
)
from mediatracker.core.precheck import (
    PRESENT,
    decide_hoster,
)
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, hoster_allowed, hoster_rank
from mediatracker.core.source_state import note_hoster_signal
from mediatracker.core.tmdb import ensure_imdb
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider
from mediatracker.events import record_event
from mediatracker.providers.base import AUTH_BLOCKED_MESSAGE, DebridError
from mediatracker.providers.sources.manual import hoster_of

_DEBRID_PRECHECK_SKIP_CODES = frozenset({"HOST_QUOTA", "LINK_HOST_UNAVAILABLE"})


def _journal_source_attempt(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    source: str,
    *,
    issue: str,
    reason: str | None = None,
    link_count: int | None = None,
    now: datetime | None = None,
) -> None:
    if db is None or row is None:
        return
    record_download_source_attempt(
        db,
        row,
        media,
        item,
        source,
        issue=issue,
        reason=reason,
        link_count=link_count,
        now=now,
    )


def _source_unavailable_reason(db: Session | None, settings: Settings, name: str) -> str | None:
    if db is None:
        return None
    if name == "hive":
        from mediatracker.hive.identity import hive_enabled_in_db

        if not hive_enabled_in_db(db):
            return "Hive désactivé."
        return None
    from mediatracker.core.source_availability import download_source_enabled_in_settings

    if not download_source_enabled_in_settings(db, settings, name):
        return "Source désactivée."
    row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == name))
    if row is not None and not row.enabled:
        return "Source désactivée."
    return None


_WAWACITY_OTHER_CATALOG_SOURCES = frozenset(
    {"free_telecharger", "comet", "torrentio", "stremio_addons"}
)
_HOSTER_LINK_KINDS = frozenset({"ddl_hoster", "ddl_protected", "direct"})


def _hoster_links_for_source(db: Session, source_type: str):
    """Stored hoster rows tied to this source key (not magnets from other catalogs)."""
    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == source_type))
    if provider is None:
        return None
    if source_type != "wawacity":
        return Link.provider_id == provider.id
    provider_ids = [provider.id]
    manual = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "manual"))
    if manual is not None:
        provider_ids.append(manual.id)
    foreign_ids = list(
        db.scalars(
            select(Provider.id).where(
                Provider.kind == "source",
                Provider.type.in_(_WAWACITY_OTHER_CATALOG_SOURCES),
            )
        ).all()
    )
    hoster_kind = Link.kind.in_(_HOSTER_LINK_KINDS)
    clauses = [Link.provider_id.in_(provider_ids), and_(Link.provider_id.is_(None), hoster_kind)]
    if foreign_ids:
        clauses.append(and_(Link.provider_id.notin_(foreign_ids), hoster_kind))
    return or_(*clauses)


def _debrid_hoster_preference(debrid, preference: list[str]) -> list[str]:
    reorder = getattr(debrid, "preference_for_cycle", None)
    if reorder is None:
        return preference
    try:
        return list(reorder(preference))
    except Exception:
        return preference


def _last_poll_rejected_by_profile(media: Media) -> bool:
    if getattr(media, "last_poll_result", None) != "nothing":
        return False
    message = getattr(media, "last_poll_message", None) or ""
    return message.startswith("Aucune version ne correspond au profil")


def _stored_links_refused_by_profile(links, preference, profile, skipped: set[int]) -> bool:
    from mediatracker.core.profile_attempt_adapters import (
        link_eligible_for_hoster_attempt,
        manual_provider_link,
        rank_link_for_profile_attempt,
    )

    eligible = False
    for link in links:
        if manual_provider_link(link):
            continue
        if not link_eligible_for_hoster_attempt(link, preference, skipped_link_ids=skipped):
            continue
        eligible = True
        if rank_link_for_profile_attempt(link, profile=profile) is not None:
            return False
    return eligible


def _attempt_bypass_profile_links(
    db,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    known: list,
    links: list[Link],
    *,
    record_used,
    record_failed,
    log_prefix: str,
    defer_final: bool = True,
    allow_manual_magnet: bool = False,
    settings: Settings | None = None,
    attempt_profile=None,
    attempt_profile_rank: int = 1,
    journal_live: dict | None = None,
):
    from mediatracker.core.magnet_claim import claim_cached_magnet
    from mediatracker.core.profile_attempt_adapters import manual_provider_link

    if not links:
        return None
    skipped = {int(value) for value in (row.skipped_link_ids or [])}
    eligible: list[Link] = []
    for link in links:
        if link.id in skipped or link.dead:
            continue
        if link.kind in {"magnet", "torrent"}:
            if allow_manual_magnet and manual_provider_link(link):
                eligible.append(link)
            continue
        eligible.append(link)
    links = eligible
    if not links:
        return None
    last_code = "NO_LINK"
    last_message = "Aucun lien utilisable."
    if journal_live is None:
        journal_live = {
            "name": getattr(attempt_profile, "name", None),
            "rank": attempt_profile_rank,
        }
    preference = _debrid_hoster_preference(debrid, preference)
    for link in links:
        if link.kind in {"magnet", "torrent"} and manual_provider_link(link):
            if debrid is None or not hasattr(debrid, "upload_magnet"):
                record_failed(
                    db,
                    row,
                    media,
                    item,
                    reason="Aucun débrideur actif.",
                    now=now,
                    link=link,
                )
                continue
            row.link_id = link.id
            row.state = "resolving"
            if row.attempt_started_at is None:
                row.attempt_started_at = now
            _commit(db)
            try:
                claimed = claim_cached_magnet(
                    debrid,
                    link.url or "",
                    file_index=getattr(link, "file_index", None),
                    filename=link.filename,
                )
            except DebridError as exc:
                record_failed(
                    db,
                    row,
                    media,
                    item,
                    reason=french_error(exc.code, exc.message),
                    now=now,
                    link=link,
                )
                if exc.code == "AUTH_BLOCKED":
                    _block_queue(db, debrid_row, now)
                    row.state = "pending"
                    row.error_code = "AUTH_BLOCKED"
                    row.error = AUTH_BLOCKED_MESSAGE
                    return "paused"
                if exc.retryable and exc.code not in _DEAD_LINK:
                    action = _schedule_retry(
                        db,
                        row,
                        exc.code,
                        french_error(exc.code, exc.message),
                        now,
                        defer_final=defer_final,
                    )
                    if action in {"exhausted", "failed"}:
                        _remember_skip(row, link.id)
                        row.link_id = None
                        last_code = exc.code
                        continue
                    return "waiting"
                _bury(db, row, link, exc.code, exc.message)
                last_code = exc.code
                continue
            if claimed is None:
                record_failed(db, row, media, item, reason="Aimant non disponible.", now=now, link=link)
                _remember_skip(row, link.id)
                row.link_id = None
                continue
            filename = _prefer_release_name(link.filename, claimed.direct.filename)
            size = claimed.direct.size or link.size
            bad = _probed_direct(claimed.direct.url, size)
            if bad:
                record_failed(db, row, media, item, reason=bad, now=now, link=link)
                _bury(db, row, link, "LINK_NOT_VIDEO", bad)
                continue
            row.debrid_magnet_id = claimed.magnet_id
            record_used(db, row, media, item, now=now, link=link)
            return (link, claimed.direct.url, filename, size, None)
        if not _hoster_supported(debrid, link):
            code, message = _debrid_skip_reason(debrid)
            if code in _DEBRID_PRECHECK_SKIP_CODES:
                record_failed(
                    db,
                    row,
                    media,
                    item,
                    reason=message or code or "hébergeur indisponible",
                    now=now,
                    link=link,
                )
                _remember_skip(row, link.id)
                row.link_id = None
                continue
            record_failed(db, row, media, item, reason=message or code, now=now, link=link)
            _bury(db, row, link, code, message)
            continue
        row.link_id = link.id
        row.state = "resolving"
        if row.attempt_started_at is None:
            row.attempt_started_at = now
        _commit(db)
        direct_url = None
        filename = link.filename
        size = link.size
        hoster_url = None
        precheck: HosterUnlockContext | None = None
        if link.kind == "direct":
            direct_url = link.url
        else:
            if debrid is None:
                record_failed(db, row, media, item, reason="Aucun débrideur actif.", now=now, link=link)
                _schedule_retry(db, row, "NO_DEBRID", "Aucun débrideur actif.", now)
                return "waiting"
            precheck = HosterUnlockContext(
                db=db,
                row=row,
                media=media,
                item=item,
                settings=settings,
                now=now,
                source_type="manual_source_page",
                attempt_profile=attempt_profile,
                attempt_profile_rank=attempt_profile_rank,
            )
            try:
                direct_url, filename, size, hoster_url = _unlock(
                    debrid, link, known, preference, precheck_ctx=precheck
                )
                bad = _probed_direct(direct_url, size or link.size)
                if bad:
                    raise DebridError("LINK_NOT_VIDEO", bad, retryable=False)
            except DebridError as exc:
                record_failed(
                    db,
                    row,
                    media,
                    item,
                    reason=french_error(exc.code, exc.message),
                    now=now,
                    link=link,
                )
                if exc.code == "AUTH_BLOCKED":
                    _block_queue(db, debrid_row, now)
                    row.state = "pending"
                    row.error_code = "AUTH_BLOCKED"
                    row.error = AUTH_BLOCKED_MESSAGE
                    return "paused"
                if exc.code == "ALREADY_PRESENT":
                    _cancel_present(db, row, now)
                    return "waiting"
                if exc.code == "PROFILE":
                    _remember_skip(row, link.id)
                    row.link_id = None
                    last_code = exc.code
                    last_message = exc.message or "filtre profil"
                    continue
                if _switch_host(exc):
                    note_hoster_signal(
                        link.hoster,
                        up=False,
                        message=french_error(exc.code, exc.message),
                    )
                    _bury(db, row, link, exc.code, exc.message)
                    last_code = exc.code
                    continue
                if exc.retryable and exc.code not in _DEAD_LINK:
                    action = _schedule_retry(
                        db,
                        row,
                        exc.code,
                        french_error(exc.code, exc.message),
                        now,
                        defer_final=defer_final,
                    )
                    if action in {"exhausted", "failed"}:
                        _remember_skip(row, link.id)
                        row.link_id = None
                        last_code = exc.code
                        continue
                    return "waiting"
                _bury(db, row, link, exc.code, exc.message)
                last_code = exc.code
                continue
        if not direct_url:
            record_failed(db, row, media, item, reason="Lien direct vide.", now=now, link=link)
            _bury(db, row, link, "LINK_EMPTY", "Lien direct vide.")
            continue
        if link.kind == "direct":
            bad = _probed_direct(direct_url, size or link.size)
            if bad:
                record_failed(db, row, media, item, reason=bad, now=now, link=link)
                _bury(db, row, link, "LINK_NOT_VIDEO", bad)
                continue
        if precheck is not None:
            if precheck.journal_profile_name:
                journal_live["name"] = precheck.journal_profile_name
            if precheck.journal_profile_rank is not None:
                journal_live["rank"] = precheck.journal_profile_rank
        note_hoster_signal(link.hoster, up=True, message="Débridage réussi.")
        record_used(db, row, media, item, now=now, link=link)
        return (link, direct_url, filename, size, hoster_url)
    logger.info(
        "%s : aucun succès après essais (job=%s episode=%s code=%s detail=%s)",
        log_prefix,
        row.id,
        _slot_label(item),
        last_code,
        last_message,
    )
    return None


def _from_manual_links(
    db,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    settings: Settings,
    known: list,
):
    """Try non-dead manual-source links first, without profile or hoster-order filters."""
    del settings
    manual = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "manual"))
    if manual is None:
        return None
    links = list(
        db.scalars(
            select(Link).where(
                Link.media_item_id == item.id,
                Link.dead.is_(False),
                Link.provider_id == manual.id,
            )
        ).all()
    )
    if not links:
        return None
    links.sort(key=lambda link: (-(link.priority or 0), link.id or 0))
    return _attempt_bypass_profile_links(
        db,
        row,
        item,
        media,
        now,
        preference,
        debrid_row,
        debrid,
        known,
        links,
        record_used=record_manual_link_used,
        record_failed=record_manual_link_failed,
        log_prefix="Liens manuels",
        defer_final=True,
        allow_manual_magnet=True,
        settings=None,
    )


def _from_manual_source_page(
    db,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    settings: Settings,
    known: list,
):
    """Read a user-bound page first: principal profile then replis (same chain as the poll)."""
    from mediatracker.core.pipeline.manual_source_page import (
        episode_candidates,
        fetch_manual_binding_link_candidates,
        link_for_manual_page_candidate,
        manual_path_binding_for_item,
        pick_manual_source_page_link,
    )
    from mediatracker.core.profile_attempt_orchestration import (
        download_attempt_profile,
        load_profile_chain,
        profile_chain_from_anchor,
    )
    from mediatracker.core.profile_fallback import ProfileResolveError

    binding = manual_path_binding_for_item(db, media, item)
    if binding is None:
        return None
    _commit(db)
    try:
        candidates = fetch_manual_binding_link_candidates(db, settings, binding)
    except Exception as exc:
        from mediatracker.providers.downloaders.aria2 import scrub_url

        detail = scrub_url(_short_exception_reason(exc)).strip()[:300]
        logger.info(
            "Page source manuelle illisible (job=%s episode=%s): %s",
            row.id,
            _slot_label(item),
            detail,
        )
        return None
    if not episode_candidates(candidates, item):
        record_manual_source_page_empty(db, row, media, item, reason="episode", now=now)
        return None
    attempt_profile = download_attempt_profile(db, media, settings, row)
    if attempt_profile is not None:
        chain = profile_chain_from_anchor(db, media, settings, attempt_profile)
    else:
        try:
            chain = load_profile_chain(db, media, settings)
        except ProfileResolveError:
            return None
    skipped = {int(value) for value in (row.skipped_link_ids or [])}
    pick = pick_manual_source_page_link(
        db,
        binding.provider_id,
        chain.profiles,
        candidates,
        item,
        preference,
        skipped_link_ids=skipped,
    )
    if pick is None:
        record_manual_source_page_empty(db, row, media, item, reason="profile", now=now)
        return None
    ordered, profile, _profile_rank_in_slice = pick
    tour_profile = attempt_profile or profile
    profile_rank = profile_rank_for_attempt(db, media, settings, tour_profile)
    stored: list[Link] = []
    try:
        for candidate in ordered:
            link = link_for_manual_page_candidate(
                db, item, candidate, binding.provider_id, preference, now
            )
            if link is None or link.dead or link.id in skipped:
                continue
            stored.append(link)
    except Exception:
        logger.info(
            "Page source manuelle : stockage lien impossible (job=%s episode=%s)",
            row.id,
            _slot_label(item),
        )
        return None
    if not stored:
        record_manual_source_page_empty(db, row, media, item, reason="profile", now=now)
        return None

    journal_live = {"name": tour_profile.name, "rank": profile_rank}

    def _journal_used(db, row, media, item, *, now=None, link=None):
        del link
        return record_manual_source_page_used(
            db,
            row,
            media,
            item,
            profile_name=journal_live["name"],
            profile_rank=int(journal_live["rank"]),
            now=now,
        )

    return _attempt_bypass_profile_links(
        db,
        row,
        item,
        media,
        now,
        preference,
        debrid_row,
        debrid,
        known,
        stored,
        record_used=_journal_used,
        record_failed=lambda *args, **kwargs: None,
        log_prefix="Page source manuelle",
        defer_final=True,
        settings=settings,
        attempt_profile=tour_profile,
        attempt_profile_rank=profile_rank,
        journal_live=journal_live,
    )


def _from_hosters(
    db,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    settings: Settings,
    known: list,
    *,
    defer_final: bool,
    source_type: str = "wawacity",
):
    """Hoster links already stored by Wawacity. None means try the next download source."""
    from mediatracker.core.profile_attempt_adapters import choose_link_for_profile_attempt
    from mediatracker.core.profile_attempt_orchestration import (
        POLL_PROFILE_MISSING,
        resolve_download_poll_profile,
    )

    resolution = resolve_download_poll_profile(db, media, settings, row)
    if resolution.missing_explicit:
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            source_type,
            issue="refused",
            reason="Profil du sondage introuvable.",
            now=now,
        )
        if defer_final:
            record_download_pass_verdict(
                row,
                source_type,
                code=POLL_PROFILE_MISSING,
                message="Profil du sondage introuvable.",
                issue="refused",
            )
            return None
        _fail(db, row, POLL_PROFILE_MISSING, "Profil du sondage introuvable.", now)
        return "waiting"
    attempt = resolution.profile
    attempt_rank = (
        profile_rank_for_attempt(db, media, settings, attempt) if attempt is not None else 1
    )
    last_code = "NO_LINK"
    last_message = "Aucun lien utilisable."
    looked_for_versions = False
    preference = _debrid_hoster_preference(debrid, preference)
    while True:
        skipped = {int(value) for value in (row.skipped_link_ids or [])}
        link_stmt = select(Link).where(Link.media_item_id == item.id, Link.dead.is_(False))
        provider_clause = _hoster_links_for_source(db, source_type)
        if provider_clause is not None:
            link_stmt = link_stmt.where(provider_clause)
        links = list(db.scalars(link_stmt).all())
        from mediatracker.core.download_bitrate_sanity import filter_stored_links

        before_links = len(links)
        links = filter_stored_links(media, item, links)
        all_filtered_by_size = before_links > 0 and not links
        if all_filtered_by_size:
            logger.info(
                "Hébergeurs : tous les liens écartés (taille/durée). job=%s episode=%s",
                row.id,
                _slot_label(item),
            )
        link = None
        if attempt is not None:
            link = choose_link_for_profile_attempt(
                preference,
                links=links,
                profile=attempt,
                skipped_link_ids=skipped,
            )
        if link is None:
            from mediatracker.core.profile_fallback import media_has_profile_repli_chain

            already_advanced = getattr(row, "__dict__", {}).get("_download_repli_advanced") is True
            if (
                not already_advanced
                and not looked_for_versions
                and attempt is not None
                and media_has_profile_repli_chain(db, media, settings)
            ):
                looked_for_versions = True
                if _ingest_other_versions(
                    db, settings, media, item, now, attempt_profile=attempt, from_next_repli=True
                ):
                    continue
            if before_links == 0:
                if last_code != "NO_LINK":
                    fail_code = last_code
                    fail_message = last_message
                    issue = "refused" if fail_code in {"SIZE_SANITY", "PROFILE", POLL_PROFILE_MISSING} else "empty"
                elif _last_poll_rejected_by_profile(media) and attempt is not None:
                    fail_code = "PROFILE"
                    fail_message = f"filtre profil — {attempt.name}"
                    issue = "refused"
                else:
                    fail_code = "NO_LINK"
                    fail_message = "aucun lien enregistré"
                    issue = "empty"
            elif all_filtered_by_size:
                fail_code = "SIZE_SANITY"
                fail_message = "Tous les liens ont une taille incompatible avec la durée attendue."
                issue = "refused"
            elif attempt is not None and _stored_links_refused_by_profile(links, preference, attempt, skipped):
                fail_code = "PROFILE"
                fail_message = "filtre profil"
                issue = "refused"
            else:
                fail_code = last_code
                fail_message = last_message
                issue = "refused" if fail_code in {"SIZE_SANITY", "PROFILE", POLL_PROFILE_MISSING} else "empty"
            _journal_source_attempt(
                db,
                row,
                media,
                item,
                source_type,
                issue=issue,
                reason=fail_message if issue == "refused" else None,
                now=now,
            )
            record_download_pass_verdict(
                row,
                source_type,
                code=fail_code,
                message=fail_message,
                issue=issue,
            )
            if defer_final:
                return None
            _fail(db, row, fail_code, french_error(fail_code, fail_message), now)
            _throttle_poll(db, media, settings, now)
            return "waiting"
        if not _hoster_supported(debrid, link):
            code, message = _debrid_skip_reason(debrid)
            if code in _DEBRID_PRECHECK_SKIP_CODES:
                logger.info("Hébergeur ignoré pour ce cycle : %s (%s)", link.hoster, message)
                _remember_skip(row, link.id)
                row.link_id = None
                _record_debrid_hoster_skip(db, row, link, code, message)
                last_code = code
                last_message = message
                continue
            _bury(db, row, link, code, message)
            _record_debrid_hoster_skip(db, row, link, code, message)
            last_code = code
            last_message = message
            continue
        row.link_id = link.id
        row.state = "resolving"
        if row.attempt_started_at is None:
            row.attempt_started_at = now
        # AllDebrid and the direct-link probe are network calls. Do not hold the write lock.
        _commit(db)
        direct_url = None
        filename = link.filename
        size = link.size
        hoster_url = None
        if link.kind == "direct":
            direct_url = link.url
        else:
            if debrid is None:
                _schedule_retry(db, row, "NO_DEBRID", "Aucun débrideur actif.", now)
                return "waiting"
            precheck = HosterUnlockContext(
                db=db,
                row=row,
                media=media,
                item=item,
                settings=settings,
                now=now,
                source_type=source_type,
                attempt_profile=attempt,
                attempt_profile_rank=attempt_rank,
            )
            try:
                direct_url, filename, size, hoster_url = _unlock(
                    debrid, link, known, preference, precheck_ctx=precheck
                )
                bad = _probed_direct(direct_url, size or link.size)
                if bad:
                    raise DebridError("LINK_NOT_VIDEO", bad, retryable=False)
            except DebridError as exc:
                if exc.code in {"REDIRECTOR_ERROR", "REDIRECTOR_NOT_SUPPORTED"}:
                    logger.info("Lien AllDebrid refusé (%s) pour job=%s.", exc.code, row.id)
                if exc.code == "AUTH_BLOCKED":
                    _block_queue(db, debrid_row, now)
                    row.state = "pending"
                    row.error_code = "AUTH_BLOCKED"
                    row.error = AUTH_BLOCKED_MESSAGE
                    return "paused"
                if exc.code == "ALREADY_PRESENT":
                    _cancel_present(db, row, now)
                    return "waiting"
                if exc.code == "PROFILE":
                    _remember_skip(row, link.id)
                    row.link_id = None
                    last_code = exc.code
                    last_message = exc.message
                    continue
                if _switch_host(exc):
                    note_hoster_signal(
                        link.hoster,
                        up=False,
                        message=french_error(exc.code, exc.message),
                    )
                    _bury(db, row, link, exc.code, exc.message)
                    last_code = exc.code
                    last_message = exc.message
                    continue
                if exc.retryable and exc.code not in _DEAD_LINK:
                    action = _schedule_retry(
                        db, row, exc.code, french_error(exc.code, exc.message), now, defer_final=defer_final
                    )
                    if action == "exhausted":
                        return None
                    return "waiting"
                _bury(db, row, link, exc.code, exc.message)
                last_code = exc.code
                last_message = exc.message
                continue
        if not direct_url:
            _bury(db, row, link, "LINK_EMPTY", "Lien direct vide.")
            last_code = "LINK_EMPTY"
            last_message = "Lien direct vide."
            continue
        if link.kind == "direct":
            bad = _probed_direct(direct_url, size or link.size)
            if bad:
                _bury(db, row, link, "LINK_NOT_VIDEO", bad)
                last_code = "LINK_NOT_VIDEO"
                last_message = bad
                continue
        note_hoster_signal(link.hoster, up=True, message="Débridage réussi.")
        _journal_source_attempt(db, row, media, item, source_type, issue="found", link_count=1, now=now)
        return (link, direct_url, filename, size, hoster_url)


def _from_named_source(
    db,
    name: str,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    settings: Settings,
    known: list,
    *,
    defer_final: bool,
):
    """Pick a consumer from the source capabilities. The name is only a registry key."""
    from mediatracker.providers.sources.registry import for_type

    entry = for_type(name)
    if entry is None:
        _journal_source_attempt(db, row, media, item, name, issue="skipped", reason="Source inconnue.", now=now)
        return None
    disabled = _source_unavailable_reason(db, settings, name)
    if disabled:
        _journal_source_attempt(db, row, media, item, name, issue="skipped", reason=disabled, now=now)
        return None
    caps = set(getattr(entry, "capabilities", ()))
    if "hive" in caps:
        return _from_hive(db, row, item, media, settings, now, defer_final=defer_final)
    if "episode" in caps:
        return _from_episode_source(
            db,
            row,
            item,
            media,
            now,
            preference,
            debrid_row,
            debrid,
            settings,
            known,
            source_type=name,
            defer_final=defer_final,
        )
    if "links" in caps:
        return _from_hosters(
            db,
            row,
            item,
            media,
            now,
            preference,
            debrid_row,
            debrid,
            settings,
            known,
            defer_final=defer_final,
            source_type=name,
        )
    return None


def _source_label(source_type: str) -> str:
    return source_type[:1].upper() + source_type[1:] if source_type else source_type


def _from_hive(
    db,
    row: Download,
    item: MediaItem,
    media: Media,
    settings: Settings,
    now: datetime,
    *,
    defer_final: bool,
):
    from mediatracker.hive.download_aria2 import prepare_hive_aria2_unlock

    return prepare_hive_aria2_unlock(db, settings, row=row, item=item, media=media, now=now, defer_final=defer_final)


def _short_exception_reason(exc: BaseException) -> str:
    name = type(exc).__name__
    detail = str(exc).strip()
    if not detail or detail == name:
        return name
    if len(detail) > 120:
        detail = f"{detail[:117]}..."
    return detail


def _record_download_source_links_error(
    db: Session,
    row: Download,
    item: MediaItem,
    media: Media,
    source_type: str,
    exc: BaseException,
) -> None:
    label = _source_label(source_type)
    reason = _short_exception_reason(exc)
    detail = f"{type(exc).__name__} : {reason}"
    _journal_source_attempt(db, row, media, item, source_type, issue="error", reason=detail)
    record_event(
        db,
        "download.source_error",
        level="warning",
        media_id=media.id,
        item_id=item.id,
        payload={
            "download_id": row.id,
            "source": source_type,
            "code": type(exc).__name__,
            "message": f"{label} : {reason}",
            "title": media.title,
            "season": item.season,
            "episode": item.episode,
        },
        dedupe_key=f"download.source_error:{row.id}:{source_type}",
    )


def _from_episode_source(
    db,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    settings: Settings,
    known: list,
    *,
    source_type: str,
    defer_final: bool = False,
):
    """Live ``links_for`` results. Magnet candidates use the cache. A miss tries the next source."""
    label = _source_label(source_type)
    if debrid is None or not hasattr(debrid, "upload_magnet"):
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            source_type,
            issue="skipped",
            reason="Débrideur indisponible.",
            now=now,
        )
        return None
    from mediatracker.providers.sources.comet_urls import resolve_comet_base_urls
    from mediatracker.providers.sources.loading import episode_source, source_base_url

    comet_urls: list[str] | None = None
    stremio_addons = None
    if source_type == "comet":
        comet_urls = resolve_comet_base_urls(db, settings)
        if not comet_urls:
            _journal_source_attempt(
                db, row, media, item, source_type, issue="skipped", reason="Comet non configuré.", now=now
            )
            return None
    elif source_type == "stremio_addons":
        from mediatracker.providers.sources.stremio_addons_config import enabled_stremio_addons

        stremio_addons = enabled_stremio_addons(db, settings)
        if not stremio_addons:
            _journal_source_attempt(
                db,
                row,
                media,
                item,
                source_type,
                issue="skipped",
                reason="Aucun addon Stremio activé.",
                now=now,
            )
            return None
    else:
        base = source_base_url(db, settings, source_type).strip()
        if not base:
            _journal_source_attempt(
                db, row, media, item, source_type, issue="skipped", reason="URL non configurée.", now=now
            )
            return None
    imdb = ensure_imdb(db, media, settings)
    if not imdb:
        logger.info("%s ignoré : pas d'identifiant IMDb pour %s.", label, media.title)
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            source_type,
            issue="skipped",
            reason="Identifiant IMDb manquant.",
            now=now,
        )
        return None
    from mediatracker.core.magnet_claim import claim_cached_magnet
    from mediatracker.providers.base import EpisodeQuery, SourceFetchError

    if comet_urls is not None:
        source = episode_source(source_type, settings, db=db, base_urls=comet_urls)
    elif stremio_addons is not None:
        source = episode_source(source_type, settings, db=db, addons=stremio_addons)
    else:
        source = episode_source(source_type, settings, db=db, base_url=base)
    if source is None:
        return None
    from mediatracker.core.profile_attempt_adapters import comet_context_for_attempt, legacy_comet_context
    from mediatracker.core.profile_attempt_orchestration import POLL_PROFILE_MISSING, resolve_download_poll_profile

    resolution = resolve_download_poll_profile(db, media, settings, row)
    if resolution.missing_explicit:
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            source_type,
            issue="refused",
            reason="Profil du sondage introuvable.",
            now=now,
        )
        record_download_pass_verdict(
            row,
            source_type,
            code=POLL_PROFILE_MISSING,
            message="Profil du sondage introuvable.",
            issue="refused",
        )
        if not defer_final:
            row.error_code = POLL_PROFILE_MISSING
            row.error = "Profil du sondage introuvable."
        return None
    poll_profile = resolution.profile
    attempt_rank = (
        profile_rank_for_attempt(db, media, settings, poll_profile)
        if poll_profile is not None
        else 1
    )
    if resolution.explicit and resolution.profile is not None:
        spec, preferences = comet_context_for_attempt(profile=resolution.profile)
    else:
        spec, preferences = legacy_comet_context(db, media, settings)
    source.quality_profile = spec
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
    rejected = _rejected_magnets(db, item)
    # Comet and AllDebrid are network calls. Do not hold the write lock across them.
    _commit(db)
    try:
        candidates = source.links_for(query, preferences)
        if source_type == "torrentio":
            from mediatracker.providers.sources.torrentio import drop_hashes_seen_in_comet

            candidates = drop_hashes_seen_in_comet(candidates, _comet_info_hashes_on_item(db, item))
    except SourceFetchError as exc:
        logger.info("%s indisponible (%s).", label, exc.code)
        reason = (exc.message or exc.code or "").strip()
        issue = "skipped" if exc.code in {"RATE_LIMIT", "THROTTLED", "TOO_MANY_REQUESTS"} else "empty"
        _journal_source_attempt(db, row, media, item, source_type, issue=issue, reason=reason or None, now=now)
        return None
    except Exception as exc:
        logger.exception("%s en erreur pendant links_for. job=%s", label, row.id)
        _record_download_source_links_error(db, row, item, media, source_type, exc)
        return None
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            close()
    from mediatracker.core.download_bitrate_sanity import filter_link_candidates

    before = len(candidates)
    candidates = filter_link_candidates(media, item, candidates)
    if before and not candidates:
        logger.info(
            "%s : aucun candidat retenu après filtre taille/durée (tous %s écarté(s)). job=%s",
            label,
            before,
            row.id,
        )
        message = "Tous les candidats ont une taille incompatible avec la durée attendue."
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            source_type,
            issue="refused",
            reason="filtre taille ou durée",
            now=now,
        )
        record_download_pass_verdict(row, source_type, code="SIZE_SANITY", message=message, issue="refused")
        if not defer_final:
            row.error_code = "SIZE_SANITY"
            row.error = message
        return None
    if not candidates:
        reason = getattr(source, "last_empty_reason", None)
        if reason:
            code = "PROFILE"
            issue = "refused"
            message = reason
            _journal_source_attempt(db, row, media, item, source_type, issue=issue, reason=reason, now=now)
        else:
            code = "NO_LINK"
            issue = "empty"
            message = format_source_attempt_message(source_type, issue=issue)
            _journal_source_attempt(db, row, media, item, source_type, issue=issue, now=now)
        record_download_pass_verdict(row, source_type, code=code, message=message, issue=issue)
        if not defer_final:
            row.error_code = code
            row.error = message
        return None
    wait = float(getattr(debrid, "cache_wait_seconds", 8.0))
    attempted = 0
    for candidate in candidates:
        if candidate.kind != "magnet":
            continue
        keys = _magnet_keys(candidate.url, getattr(candidate, "external_id", None))
        if keys & rejected:
            continue
        if attempted >= COMET_PER_CYCLE:
            # More magnets remain. Stop this cycle so the tick is not a tight loop.
            row.state = "pending"
            if _aware(row.next_retry_at) is None or _aware(row.next_retry_at) <= now:
                row.next_retry_at = now + COMET_BACKOFFS[0]
            if host_down(row.error_code, row.error):
                row.error_code = None
                row.error = None
            _commit(db)
            logger.info(
                "%s en pause après %s aimants. job=%s episode=%s",
                label,
                attempted,
                row.id,
                _slot_label(item),
            )
            return "waiting"
        attempted += 1
        _commit(db)
        try:
            claimed = claim_cached_magnet(
                debrid,
                candidate.url,
                file_index=candidate.file_index,
                filename=candidate.filename,
                wait_seconds=wait,
            )
        except DebridError as exc:
            if exc.code == "AUTH_BLOCKED":
                _block_queue(db, debrid_row, now)
                row.state = "pending"
                row.error_code = "AUTH_BLOCKED"
                row.error = AUTH_BLOCKED_MESSAGE
                return "paused"
            if exc.code in _PERMANENT_MAGNET:
                _reject_source_magnet(db, row, item, candidate, exc.code, exc.message, now, source_type)
                rejected |= keys
                continue
            logger.info(
                "Aimant %s refusé (%s). job=%s episode=%s hash=%s",
                label,
                exc.code,
                row.id,
                _slot_label(item),
                _hash_label(keys),
            )
            if exc.retryable:
                _schedule_retry(db, row, exc.code, french_error(exc.code, exc.message), now)
                return "waiting"
            continue
        if claimed is None:
            continue
        if host_down(row.error_code, row.error):
            row.error_code = None
            row.error = None
        filename = _prefer_release_name(candidate.filename, claimed.direct.filename)
        size = claimed.direct.size or candidate.size
        bad = _probed_direct(claimed.direct.url, size)
        if bad:
            _reject_source_magnet(db, row, item, candidate, "LINK_NOT_VIDEO", bad, now, source_type)
            rejected |= keys
            continue
        link = _store_source_link(db, item, candidate, now, source_type)
        row.link_id = link.id
        row.debrid_magnet_id = claimed.magnet_id
        row.state = "resolving"
        if row.attempt_started_at is None:
            row.attempt_started_at = now
        _journal_source_attempt(db, row, media, item, source_type, issue="found", link_count=len(candidates), now=now)
        return (link, claimed.direct.url, filename, size, None)
    for candidate in candidates:
        if candidate.kind not in _HOSTER_LINK_KINDS:
            continue
        if not hoster_allowed(candidate.hoster, preference):
            continue
        _commit(db)
        link = _store_source_link(db, item, candidate, now, source_type)
        row.link_id = link.id
        row.state = "resolving"
        if row.attempt_started_at is None:
            row.attempt_started_at = now
        if debrid is None:
            _schedule_retry(db, row, "NO_DEBRID", "Aucun débrideur actif.", now)
            return "waiting"
        if not _hoster_supported(debrid, link):
            continue
        precheck = HosterUnlockContext(
            db=db,
            row=row,
            media=media,
            item=item,
            settings=settings,
            now=now,
            source_type=source_type,
            attempt_profile=poll_profile,
            attempt_profile_rank=attempt_rank,
        )
        try:
            direct_url, filename, size, hoster_url = _unlock(
                debrid, link, known, preference, precheck_ctx=precheck
            )
            bad = _probed_direct(direct_url, size or link.size)
            if bad:
                raise DebridError("LINK_NOT_VIDEO", bad, retryable=False)
        except DebridError as exc:
            if exc.code == "AUTH_BLOCKED":
                _block_queue(db, debrid_row, now)
                row.state = "pending"
                row.error_code = "AUTH_BLOCKED"
                row.error = AUTH_BLOCKED_MESSAGE
                return "paused"
            if exc.code == "ALREADY_PRESENT":
                _cancel_present(db, row, now)
                return "waiting"
            if exc.code == "PROFILE":
                _remember_skip(row, link.id)
                row.link_id = None
                continue
            if _switch_host(exc):
                note_hoster_signal(
                    link.hoster,
                    up=False,
                    message=french_error(exc.code, exc.message),
                )
                continue
            if exc.retryable and exc.code not in _DEAD_LINK:
                action = _schedule_retry(
                    db, row, exc.code, french_error(exc.code, exc.message), now, defer_final=defer_final
                )
                if action == "exhausted":
                    return None
                return "waiting"
            continue
        if not direct_url:
            continue
        note_hoster_signal(link.hoster, up=True, message="Débridage réussi.")
        _journal_source_attempt(db, row, media, item, source_type, issue="found", link_count=len(candidates), now=now)
        return (link, direct_url, filename, size, hoster_url)
    if rejected:
        row.error_code = "DISCONTINUED"
        row.error = french_error("DISCONTINUED", row.error)
        # The rejection already committed a backoff. Keep it if this fail commit is lost.
        if _aware(row.next_retry_at) is None or _aware(row.next_retry_at) <= now:
            row.next_retry_at = now + COMET_BACKOFFS[0]
    return None


def _store_source_link(db: Session, item: MediaItem, candidate, now: datetime, source_type: str) -> Link:
    from mediatracker.providers.sources.loading import download_source_rank

    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == source_type))
    if provider is None:
        provider = Provider(
            kind="source",
            type=source_type,
            name=_source_label(source_type),
            enabled=True,
            priority=download_source_rank(source_type),
            config={},
            builtin=True,
            created_at=now,
        )
        db.add(provider)
        db.flush()
    existing = db.scalar(select(Link).where(Link.provider_id == provider.id, Link.external_id == candidate.external_id))
    if existing is not None:
        # A discontinued magnet stays dead until the person retries the job.
        if not (existing.dead and _magnet_stays_dead(existing.last_error)):
            existing.dead = False
            existing.url = candidate.url
            existing.kind = getattr(candidate, "kind", None) or existing.kind
            existing.hoster = getattr(candidate, "hoster", None) or existing.hoster
            existing.filename = candidate.filename
            existing.quality = candidate.quality
            existing.language = candidate.language
            existing.size = candidate.size
            existing.media_item_id = item.id
            existing.last_error = None
        return existing
    kind = getattr(candidate, "kind", None) or "magnet"
    link = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        kind=kind,
        url=candidate.url,
        hoster=getattr(candidate, "hoster", None),
        quality=candidate.quality,
        language=candidate.language,
        size=candidate.size,
        filename=candidate.filename,
        external_id=candidate.external_id,
        discovered_at=now,
    )
    db.add(link)
    db.flush()
    return link


def _reject_source_magnet(
    db: Session,
    row: Download,
    item: MediaItem,
    candidate,
    code: str,
    message: str,
    now: datetime,
    source_type: str,
) -> None:
    """Remember this magnet as tried. The next tick must not upload it again."""
    label = _source_label(source_type)
    from mediatracker.core.dead_links import mark_link_dead

    link = _store_source_link(db, item, candidate, now, source_type)
    mark_link_dead(link, code, french_error(code, message), now)
    _remember_skip(row, link.id)
    row.link_id = None
    row.error_code = code
    row.error = link.last_error
    row.state = "pending"
    strikes = _discontinued_strikes(db, item)
    row.next_retry_at = now + COMET_BACKOFFS[min(max(strikes, 1) - 1, len(COMET_BACKOFFS) - 1)]
    keys = _magnet_keys(candidate.url, getattr(candidate, "external_id", None))
    logger.info(
        "Aimant %s écarté (%s). job=%s episode=%s hash=%s",
        label,
        code,
        row.id,
        _slot_label(item),
        _hash_label(keys),
    )
    # Persist the hash and the backoff inside the rejection. A later fail commit
    # can be lost on a SQLite lock; the next tick must still wait.
    _commit(db)


def _stored_release_names(db: Session, item: MediaItem) -> list[str]:
    """Release filenames already stored on this item's Wawacity links."""
    names: list[str] = []
    rows = db.scalars(select(Link).where(Link.media_item_id == item.id)).all()
    for link in rows:
        name = (link.filename or "").strip()
        if not name or "." not in name or name in names:
            continue
        names.append(name)
        if len(names) == 8:
            break
    return names


def _comet_info_hashes_on_item(db: Session, item: MediaItem) -> set[str]:
    """Info hashes already tied to Comet links on this episode (dedup Torrentio)."""
    from sqlalchemy import select

    from mediatracker.db.models import Link, Provider

    keys: set[str] = set()
    rows = db.scalars(
        select(Link)
        .join(Provider, Link.provider_id == Provider.id)
        .where(Link.media_item_id == item.id, Provider.type == "comet")
    ).all()
    for link in rows:
        keys |= _magnet_keys(link.url, link.external_id)
    return keys


def _rejected_magnets(db: Session, item: MediaItem) -> set[str]:
    keys: set[str] = set()
    rows = db.scalars(select(Link).where(Link.media_item_id == item.id, Link.dead.is_(True))).all()
    for link in rows:
        if not _magnet_stays_dead(link.last_error):
            continue
        keys |= _magnet_keys(link.url, link.external_id)
    return keys


def _magnet_stays_dead(last_error: str | None) -> bool:
    text = last_error or ""
    return "DISCONTINUED" in text or "LINK_NOT_VIDEO" in text or "n'est pas une vidéo" in text


def _discontinued_strikes(db: Session, item: MediaItem) -> int:
    rows = db.scalars(select(Link).where(Link.media_item_id == item.id, Link.dead.is_(True))).all()
    return sum(1 for link in rows if "DISCONTINUED" in (link.last_error or ""))


def _slot_label(item: MediaItem) -> str:
    if item.season is not None and item.episode is not None:
        return f"S{item.season:02d}E{item.episode:02d}"
    return "film"


def _hash_label(keys: set[str]) -> str:
    if not keys:
        return "?"
    return min(keys, key=len)


def _btih_hex(token: str) -> str | None:
    """Hex or base32 BitTorrent info-hash, stored as 40 lowercase hex chars."""
    raw = token.strip().lower()
    if re.fullmatch(r"[a-f0-9]{40}", raw):
        return raw
    if re.fullmatch(r"[a-z2-7]{32}", raw):
        try:
            return base64.b32decode(raw.upper()).hex()
        except Exception:
            return None
    return None


def _magnet_keys(url: str | None, external_id: str | None) -> set[str]:
    """Stable info-hash keys. Hex and base32 btih of the same torrent match."""
    keys: set[str] = set()
    for blob in (url or "", external_id or ""):
        for pattern in (_BTIH, _MAGNET_SOURCE_HASH):
            for match in pattern.finditer(blob):
                hexed = _btih_hex(match.group(1))
                if hexed:
                    keys.add(hexed)
        bare = _btih_hex(blob)
        if bare:
            keys.add(bare)
    return keys


def _virtual_redirect(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == "redirect.alldebrid.com"


def _blocked_unlock_host(host: str | None) -> bool:
    return bool(host) and host.casefold() in BLOCKED_SOURCE_HOSTERS


def _redirector_targets(urls: list[str], preference: list[str]) -> list[str]:
    """Keep every extracted http link except blocked hosters.

    AllDebrid often returns ``https://redirect.alldebrid.com/...`` instead of
    ``rapidgator.net``. Unknown domains (``trbt.cc``, ``rg.to``) used to be
    dropped, which became « le redirecteur n'a rien extrait ». Each remaining
    link is unlocked on its own.
    """
    real: list[str] = []
    virtual: list[str] = []
    for url in urls:
        if not url.lower().startswith(("http://", "https://")):
            continue
        if _virtual_redirect(url):
            virtual.append(url)
            continue
        hoster = hoster_of(url)
        if _blocked_unlock_host(hoster):
            continue
        if hoster and not hoster_allowed(hoster, preference):
            continue
        real.append(url)
    real.sort(key=lambda url: hoster_rank(hoster_of(url), preference))
    return real + virtual


def _switch_host(exc: DebridError) -> bool:
    """A down host is not a pause on the same link. The next hoster runs now."""
    return host_down(exc.code, exc.message)


def _unlock(
    debrid,
    link: Link,
    known: list,
    preference: list[str],
    *,
    precheck_ctx: HosterUnlockContext | None = None,
) -> tuple[str | None, str | None, int | None, str]:
    targets = [link.url]
    if link.kind == "ddl_protected":
        targets = _redirector_targets(list(debrid.resolve(link.url)), preference)
        if not targets:
            logger.info("Redirecteur AllDebrid sans lien utilisable (REDIRECTOR_ERROR).")
            raise DebridError("REDIRECTOR_ERROR", "Le redirecteur n'a pas pu extraire les liens.", retryable=False)
    last: DebridError | None = None
    for url in targets:
        if _virtual_redirect(url):
            try:
                direct = debrid.unlock(url)
            except DebridError as exc:
                logger.info("Débridage du lien redirecteur refusé (%s).", exc.code)
                last = exc
                if exc.code == "AUTH_BLOCKED" or (exc.retryable and not host_down(exc.code, exc.message)):
                    raise
                last = DebridError(exc.code, exc.message, retryable=False)
                continue
            if _blocked_unlock_host(getattr(direct, "host", None)):
                last = DebridError(
                    "LINK_HOST_NOT_SUPPORTED",
                    "Hébergeur non pris en charge.",
                    retryable=False,
                )
                continue
            filename = _prefer_release_name(link.filename, direct.filename)
            size = direct.size or link.size
            return direct.url, filename, size, url
        decision = decide_hoster(debrid, url, link.filename, known)
        if decision.action == "paused":
            raise DebridError("AUTH_BLOCKED", decision.message or AUTH_BLOCKED_MESSAGE)
        if decision.action == "retry":
            raise DebridError(
                decision.code or "LINK_HOST_UNAVAILABLE",
                decision.message or "Hébergeur indisponible.",
                retryable=True,
            )
        if decision.action == "present":
            raise DebridError("ALREADY_PRESENT", PRESENT, retryable=False)
        if decision.action == "offline":
            last = DebridError(decision.code or "LINK_DOWN", decision.message or "hors ligne", retryable=False)
            continue
        apply_hoster_inspection_to_link(
            link,
            filename=decision.filename,
            size=decision.size,
            ctx=precheck_ctx,
        )
        try:
            direct = debrid.unlock(url)
        except DebridError as exc:
            last = exc
            if exc.code == "AUTH_BLOCKED" or (exc.retryable and not host_down(exc.code, exc.message)):
                raise
            last = DebridError(exc.code, exc.message, retryable=False)
            continue
        filename = _prefer_release_name(link.filename, decision.filename or direct.filename)
        size = decision.size or direct.size or link.size
        return direct.url, filename, size, url
    if last is not None:
        raise last
    raise DebridError("LINK_EMPTY", "Le redirecteur n'a renvoyé aucun lien.", retryable=True)


def _debrid_skip_reason(debrid) -> tuple[str, str]:
    pop = getattr(debrid, "pop_skip_reason", None)
    if pop is not None:
        reason = pop()
        if reason:
            return reason
    return "LINK_HOST_NOT_SUPPORTED", "Hébergeur non pris en charge."


def _record_debrid_hoster_skip(db: Session, row: Download, link: Link, code: str, message: str) -> None:
    item = db.get(MediaItem, row.media_item_id)
    media = db.get(Media, item.media_id) if item is not None else None
    record_event(
        db,
        "debrid.hoster_skipped",
        media_id=media.id if media is not None else None,
        item_id=row.media_item_id,
        payload={
            "download_id": row.id,
            "link_id": link.id,
            "hoster": link.hoster,
            "code": code,
            "message": message,
            "title": media.title if media is not None else None,
            "season": item.season if item is not None else None,
            "episode": item.episode if item is not None else None,
        },
        dedupe_key=f"debrid-skip:{row.id}:{link.id}:{code}",
    )


def _prefer_release_name(stored: str | None, unlocked: str | None) -> str | None:
    """Keep the Wawacity release filename instead of AllDebrid's random CDN name."""
    name = (stored or "").strip()
    if name and "." in name and _RELEASE_HINT.search(name):
        return name
    return unlocked or stored


def _announced_length(headers: httpx.Headers, status: int) -> int | None:
    content_range = headers.get("content-range") or ""
    found = re.search(r"/(\d+)\s*$", content_range)
    if found:
        return int(found.group(1))
    if status == 200:
        raw = headers.get("content-length")
        if raw and raw.isdigit():
            return int(raw)
    return None


def _probed_direct(url, expected, **kwargs):
    """Late lookup so tests can replace ``mediatracker.core.pipeline._direct_reject_reason``."""
    import mediatracker.core.pipeline as pipeline

    return pipeline._direct_reject_reason(url, expected, **kwargs)


def _direct_reject_reason(
    url: str | None,
    expected: int | None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """Reject an unlocked link that is HTML or far smaller than the release.

    A connection error fails open: tests and a briefly unreachable CDN must
    not block a real video. The CDN URL is never logged.
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        timeout = httpx.Timeout(2.0, connect=0.4)
        with httpx.Client(timeout=timeout, follow_redirects=True, max_redirects=2, transport=transport) as client:
            with client.stream("GET", url, headers={"Range": "bytes=0-511", "Accept": "*/*"}) as response:
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                length = _announced_length(response.headers, response.status_code)
                chunk = b""
                for part in response.iter_bytes(64):
                    chunk += part
                    if len(chunk) >= 64:
                        break
                if response.status_code >= 400 and not chunk and not content_type:
                    return None
    except httpx.HTTPError:
        return None
    if any(content_type.startswith(kind) for kind in _HTML_TYPES):
        return _FRENCH["LINK_NOT_VIDEO"]
    head = chunk.lstrip()[:24].lower()
    if head.startswith(_HTML_MAGIC) or head.startswith(b"{"):
        return _FRENCH["LINK_NOT_VIDEO"]
    floor = 1024 * 1024
    if expected is not None and expected >= floor and length is not None and length < max(floor, expected // 10):
        return _FRENCH["LINK_NOT_VIDEO"]
    return None
