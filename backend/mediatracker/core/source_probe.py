"""Test manuel des sources de téléchargement (réglages). Aucun fichier ni file d'attente."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.download_bitrate_sanity import filter_link_candidates
from mediatracker.core.follow import default_pref_language, default_pref_quality
from mediatracker.core.pipeline.jobs import _text_setting
from mediatracker.core.profile_attempt_adapters import legacy_comet_context
from mediatracker.core.quality_profile import ensure_default_profile, preferences_for, profile_from_row
from mediatracker.core.selection import (
    BLOCKED_SOURCE_HOSTERS,
    hoster_allowed,
    parse_hoster_preference,
)
from mediatracker.core.source_state import (
    _source_label,
    note_probe_source_health,
    public_source_message,
    record_source_probe,
)
from mediatracker.core.tmdb.imdb import normalize_imdb_id
from mediatracker.db.models import Media, MediaItem, Provider, QualityProfile
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.library.parse import slot_from_name
from mediatracker.providers.base import EpisodeQuery, SourceFetchError, SourcePreferences, SourceRef
from mediatracker.providers.sources.loading import close_source, episode_source, remote_links_source, source_base_url
from mediatracker.providers.sources.registry import for_type

_SLOT_TAIL = re.compile(r"(?i)(?:\s|\.|_|-)*(?:s\d{1,2}e\d{1,2}|saison\s+\d+\s+ep(?:isode)?\s+\d+)\s*$")
_YEAR_IN_PARENS = re.compile(r"^(.*)\s*\((\d{4})\)\s*$")


@dataclass(frozen=True)
class SourceProbeTarget:
    title: str
    media_type: str
    season: int | None
    episode: int | None
    imdb_id: str | None
    tmdb_id: int | None = None
    year: int | None = None
    original_title: str | None = None


@dataclass(frozen=True)
class SourceProbeRow:
    source: str
    label: str
    issue: str
    link_count: int
    reason: str | None
    duration_ms: int


@dataclass(frozen=True)
class ProbeRunResult:
    results: list[SourceProbeRow]
    profile_name: str | None


@dataclass(frozen=True)
class _DeferredProbePersistence:
    type_name: str
    issue: str
    link_count: int
    duration_ms: int
    reason: str | None


def parse_probe_query(
    raw: str,
    *,
    imdb_id: str | None = None,
    tmdb_id: int | str | None = None,
) -> SourceProbeTarget:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Indiquez un titre.")
    season, episode = slot_from_name(text)
    title = _SLOT_TAIL.sub("", text).strip() or text
    title, year_hint = _title_and_year_from_text(title)
    media_type = "series" if season is not None and episode is not None else "film"
    clean_imdb: str | None = None
    if (imdb_id or "").strip():
        clean_imdb = normalize_imdb_id(imdb_id or "")
    clean_tmdb: int | None = None
    if tmdb_id is not None and str(tmdb_id).strip():
        try:
            parsed = int(str(tmdb_id).strip())
        except ValueError as exc:
            raise ValueError("Identifiant TMDB invalide.") from exc
        if parsed <= 0:
            raise ValueError("Identifiant TMDB invalide.")
        clean_tmdb = parsed
    return SourceProbeTarget(
        title=title,
        media_type=media_type,
        season=season,
        episode=episode,
        imdb_id=clean_imdb,
        tmdb_id=clean_tmdb,
        year=year_hint,
        original_title=None,
    )


def _title_and_year_from_text(text: str) -> tuple[str, int | None]:
    match = _YEAR_IN_PARENS.match(text.strip())
    if not match:
        return text.strip(), None
    head = (match.group(1) or "").strip()
    try:
        year = int(match.group(2))
    except ValueError:
        return text.strip(), None
    if year < 1900 or year > 2100:
        return text.strip(), None
    return head or text.strip(), year


def enrich_probe_target(db: Session, settings: Settings, target: SourceProbeTarget) -> SourceProbeTarget:
    """Année, titres et IMDb pour la sonde (TMDB puis IMDb), sans écrire en base."""
    if target.tmdb_id is None:
        return target
    from mediatracker.core.tmdb import ensure_imdb
    from mediatracker.core.tmdb.fetch import fetch_title

    title = target.title
    year = target.year
    original = target.original_title
    fetched = fetch_title(db, settings, target.tmdb_id, target.media_type)
    if fetched is not None:
        title = (fetched.title or title).strip() or title
        if fetched.year is not None:
            year = fetched.year
        original = (fetched.original_title or "").strip() or original
    imdb = target.imdb_id
    raw_imdb = (fetched.imdb_id or "").strip() if fetched is not None else ""
    if raw_imdb:
        if raw_imdb.startswith("tt"):
            imdb = raw_imdb
        else:
            try:
                imdb = normalize_imdb_id(raw_imdb)
            except ValueError:
                pass
    if not imdb:
        media = Media(
            type=target.media_type,
            title=title,
            year=year,
            original_title=original,
            followed=True,
            tmdb_id=target.tmdb_id,
        )
        imdb = ensure_imdb(db, media, settings)
    return SourceProbeTarget(
        title=title,
        media_type=target.media_type,
        season=target.season,
        episode=target.episode,
        imdb_id=imdb,
        tmdb_id=target.tmdb_id,
        year=year,
        original_title=original,
    )


def _probe_imdb_missing_message(target: SourceProbeTarget) -> str:
    if target.tmdb_id is not None:
        return "pas d'ID IMDb pour ce titre"
    return "identifiant TMDB ou IMDb requis pour cette source"


def _effective_probe_order(db: Session, settings: Settings) -> list[str]:
    from mediatracker.core.source_availability import global_enabled_download_sources

    return global_enabled_download_sources(db, settings)


def _source_disabled(db: Session, name: str, settings: Settings) -> str | None:
    from mediatracker.core.source_availability import source_unusable_reason

    blocked = source_unusable_reason(db, settings, name, for_manual_probe=True)
    if blocked:
        return blocked
    row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == name))
    if row is not None and not row.enabled:
        return "Source désactivée."
    entry = for_type(name)
    if entry is None:
        return "Source inconnue."
    caps = set(getattr(entry, "capabilities", frozenset()))
    if "episode" in caps or name == "comet":
        if name == "comet":
            from mediatracker.providers.sources.comet_urls import resolve_comet_base_urls

            if not resolve_comet_base_urls(db, settings):
                return "Comet non configuré."
        elif name == "stremio_addons":
            from mediatracker.providers.sources.stremio_addons_config import enabled_stremio_addons

            if not enabled_stremio_addons(db, settings):
                return "Aucun addon Stremio activé."
        else:
            if not source_base_url(db, settings, name).strip():
                return "URL non configurée."
    if "links" in caps:
        if not source_base_url(db, settings, name).strip():
            return "URL non configurée."
    return None


def _default_profile(db: Session) -> object | None:
    ensure_default_profile(db)
    row = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)))
    if row is None:
        return None
    return profile_from_row(row)


def _persist_probe_batch(db: Session, batch: list[_DeferredProbePersistence], moment: datetime) -> None:
    """Écritures probe / santé regroupées après les phases réseau."""
    for item in batch:
        record_source_probe(
            db,
            type_name=item.type_name,
            issue=item.issue,
            link_count=item.link_count,
            duration_ms=item.duration_ms,
            reason=item.reason,
            when=moment,
        )
        note_probe_source_health(
            db,
            type_name=item.type_name,
            issue=item.issue,
            reason=item.reason,
            when=moment,
        )


def _finalize_probe_reason(source: object, reason: str | None) -> str | None:
    text = (reason or "").strip()
    if not text:
        return reason
    if getattr(source, "last_json_from_cache", False):
        return f"{text} — résultat en cache"
    return reason


def _count_hoster_links(candidates, preference: list[str]) -> tuple[int, str | None]:
    kept = 0
    for item in candidates:
        hoster = (getattr(item, "hoster", None) or "").lower()
        if hoster in BLOCKED_SOURCE_HOSTERS:
            continue
        if not hoster_allowed(hoster, preference):
            continue
        kept += 1
    if not candidates:
        return 0, None
    if candidates and kept == 0:
        return 0, "aucun lien compatible avec les hébergeurs autorisés"
    return kept, None


def probe_download_sources(
    db: Session,
    settings: Settings,
    target: SourceProbeTarget,
    *,
    now: datetime | None = None,
    sources: list[str] | None = None,
) -> ProbeRunResult:
    """Interroge chaque source active de l'ordre global, sans lancer de téléchargement."""
    target = enrich_probe_target(db, settings, target)
    moment = now or datetime.now(timezone.utc)
    order = _effective_probe_order(db, settings)
    if sources:
        allowed = {(part or "").strip().lower() for part in sources if (part or "").strip()}
        order = [name for name in order if name in allowed]
    preference = parse_hoster_preference(_text_setting(db, "hoster_preference", settings.hoster_preference or ""))
    media = _stub_media(target)
    profile = _default_profile(db)
    spec, comet_prefs = legacy_comet_context(db, media, settings)
    item = _stub_item(target)
    rows: list[SourceProbeRow] = []
    pending: list[_DeferredProbePersistence] = []
    release_sqlite_before_network(db)
    for name in order:
        if name == "stremio_addons":
            release_sqlite_before_network(db)
            addon_rows, addon_pending = _probe_stremio_addons(
                db,
                settings,
                target,
                media,
                item,
                spec=spec,
                comet_prefs=comet_prefs,
            )
            rows.extend(addon_rows)
            pending.extend(addon_pending)
            continue
        label = _source_label(name)
        disabled = _source_disabled(db, name, settings)
        if disabled:
            rows.append(SourceProbeRow(name, label, "skipped", 0, disabled, 0))
            continue
        release_sqlite_before_network(db)
        started = time.perf_counter()
        try:
            row = _probe_one(
                db,
                settings,
                name,
                target,
                media,
                item,
                preference=preference,
                profile=profile,
                spec=spec,
                comet_prefs=comet_prefs,
            )
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            reason = public_source_message(f"{type(exc).__name__} : {exc}")
            rows.append(SourceProbeRow(name, label, "error", 0, reason, duration))
            pending.append(
                _DeferredProbePersistence(name, "error", 0, duration, reason),
            )
            continue
        duration = int((time.perf_counter() - started) * 1000)
        probe_row = SourceProbeRow(name, label, row.issue, row.link_count, row.reason, duration)
        rows.append(probe_row)
        pending.append(
            _DeferredProbePersistence(
                name,
                probe_row.issue,
                probe_row.link_count,
                probe_row.duration_ms,
                probe_row.reason,
            ),
        )
    if pending:
        _persist_probe_batch(db, pending, moment)
    profile_name = getattr(spec, "name", None) if spec is not None else None
    return ProbeRunResult(rows, profile_name)


@dataclass(frozen=True)
class _ProbeOutcome:
    issue: str
    link_count: int
    reason: str | None


def _stub_media(target: SourceProbeTarget) -> Media:
    return Media(
        type=target.media_type,
        title=target.title,
        year=target.year,
        original_title=target.original_title,
        followed=True,
        imdb_id=target.imdb_id,
        tmdb_id=target.tmdb_id,
    )


def _stub_item(target: SourceProbeTarget) -> MediaItem:
    return MediaItem(
        season=target.season if target.media_type == "series" else None,
        episode=target.episode if target.media_type == "series" else None,
        state="missing",
    )


def _probe_one(
    db: Session,
    settings: Settings,
    name: str,
    target: SourceProbeTarget,
    media: Media,
    item: MediaItem,
    *,
    preference: list[str],
    profile,
    spec,
    comet_prefs,
) -> _ProbeOutcome:
    entry = for_type(name)
    if entry is None:
        return _ProbeOutcome("skipped", 0, "Source inconnue.")
    caps = set(getattr(entry, "capabilities", frozenset()))
    if "hive" in caps:
        return _probe_hive(db, settings, media, item, profile=profile)
    if "episode" in caps:
        return _probe_episode(db, settings, name, target, media, item, spec=spec, comet_prefs=comet_prefs)
    if "links" in caps:
        return _probe_links(db, settings, name, media, item, preference=preference, profile=profile)
    return _ProbeOutcome("skipped", 0, "Capacité non supportée.")


def _probe_hive(db: Session, settings: Settings, media: Media, item: MediaItem, *, profile) -> _ProbeOutcome:
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain, pick_hive_catalog_for_item

    if profile is None:
        return _ProbeOutcome("refused", 0, "profil qualité manquant")
    try:
        chain = load_profile_chain(db, media, settings)
    except Exception:
        return _ProbeOutcome("empty", 0, None)
    pick = pick_hive_catalog_for_item(db, media, item, chain, single_profile=profile)
    if pick is None:
        return _ProbeOutcome("empty", 0, None)
    return _ProbeOutcome("found", 1, None)


def _probe_stremio_addons(
    db: Session,
    settings: Settings,
    target: SourceProbeTarget,
    media: Media,
    item: MediaItem,
    *,
    spec,
    comet_prefs,
) -> tuple[list[SourceProbeRow], list[_DeferredProbePersistence]]:
    from mediatracker.providers.sources.stremio_addons_config import (
        addon_display_label,
        addon_probe_source_id,
        enabled_stremio_addons,
    )

    rows: list[SourceProbeRow] = []
    pending: list[_DeferredProbePersistence] = []
    blocked = _source_disabled(db, "stremio_addons", settings)
    if blocked:
        rows.append(SourceProbeRow("stremio_addons", _source_label("stremio_addons"), "skipped", 0, blocked, 0))
        return rows, pending
    entries = enabled_stremio_addons(db, settings)
    if not media.imdb_id:
        message = _probe_imdb_missing_message(target)
        for index, entry in enumerate(entries):
            label = addon_display_label(entry, index)
            rows.append(
                SourceProbeRow(addon_probe_source_id(index), label, "skipped", 0, message, 0)
            )
        return rows, pending
    from mediatracker.core.download_bitrate_sanity import filter_link_candidates
    from mediatracker.providers.base import EpisodeQuery, SourceFetchError
    from mediatracker.providers.sources.loading import close_source, episode_source

    query = EpisodeQuery(
        title=media.title,
        original_title=media.original_title,
        year=media.year,
        media_type=media.type if media.type in {"film", "series"} else "film",
        imdb_id=media.imdb_id,
        season=item.season,
        episode=item.episode,
        release_names=[],
    )
    prefs = preferences_for(spec) if spec else comet_prefs
    for index, entry in enumerate(entries):
        label = addon_display_label(entry, index)
        source_id = addon_probe_source_id(index)
        started = time.perf_counter()
        source = episode_source("stremio_addons", settings, db=db, addons=[entry])
        if source is None:
            duration = int((time.perf_counter() - started) * 1000)
            rows.append(SourceProbeRow(source_id, label, "skipped", 0, "addon indisponible", duration))
            continue
        try:
            if spec is not None:
                source.quality_profile = spec
            candidates = list(source.links_for(query, prefs))
            before = len(candidates)
            candidates = filter_link_candidates(media, item, candidates)
            duration = int((time.perf_counter() - started) * 1000)
            if before and not candidates:
                outcome = _ProbeOutcome("refused", 0, "filtre taille ou durée")
            elif not candidates:
                reason = _finalize_probe_reason(source, getattr(source, "last_empty_reason", None))
                if reason:
                    outcome = _ProbeOutcome("refused", 0, reason)
                elif getattr(source, "last_json_from_cache", False):
                    outcome = _ProbeOutcome("empty", 0, "résultat en cache")
                else:
                    outcome = _ProbeOutcome("empty", 0, None)
            else:
                outcome = _ProbeOutcome("found", len(candidates), None)
        except SourceFetchError as exc:
            duration = int((time.perf_counter() - started) * 1000)
            outcome = _ProbeOutcome("error", 0, public_source_message(exc.message or exc.code or ""))
        finally:
            close_source(source)
        rows.append(
            SourceProbeRow(source_id, label, outcome.issue, outcome.link_count, outcome.reason, duration)
        )
        pending.append(
            _DeferredProbePersistence(
                "stremio_addons",
                outcome.issue,
                outcome.link_count,
                duration,
                outcome.reason,
            )
        )
    return rows, pending


def _probe_episode(
    db: Session,
    settings: Settings,
    name: str,
    target: SourceProbeTarget,
    media: Media,
    item: MediaItem,
    *,
    spec,
    comet_prefs,
) -> _ProbeOutcome:
    if not media.imdb_id:
        return _ProbeOutcome("skipped", 0, _probe_imdb_missing_message(target))
    from mediatracker.providers.sources.comet_urls import resolve_comet_base_urls

    comet_urls: list[str] | None = None
    if name == "comet":
        comet_urls = resolve_comet_base_urls(db, settings)
        if not comet_urls:
            return _ProbeOutcome("skipped", 0, "Comet non configuré")
        source = episode_source(name, settings, db=db, base_urls=comet_urls)
    else:
        base = source_base_url(db, settings, name).strip()
        if not base:
            return _ProbeOutcome("skipped", 0, "URL non configurée")
        source = episode_source(name, settings, db=db, base_url=base)
    if source is None:
        return _ProbeOutcome("skipped", 0, "source indisponible")
    try:
        query = EpisodeQuery(
            title=media.title,
            original_title=media.original_title,
            year=media.year,
            media_type=media.type if media.type in {"film", "series"} else "film",
            imdb_id=media.imdb_id,
            season=item.season,
            episode=item.episode,
            release_names=[],
        )
        prefs = comet_prefs if name == "comet" else preferences_for(spec) if spec else SourcePreferences()
        if spec is not None:
            source.quality_profile = spec
        candidates = list(source.links_for(query, prefs))
        if name == "torrentio":
            from mediatracker.providers.sources.torrentio import drop_hashes_seen_in_comet

            candidates = drop_hashes_seen_in_comet(candidates, set())
        before = len(candidates)
        candidates = filter_link_candidates(media, item, candidates)
        if before and not candidates:
            return _ProbeOutcome("refused", 0, "filtre taille ou durée")
        if not candidates:
            reason = _finalize_probe_reason(source, getattr(source, "last_empty_reason", None))
            if reason:
                return _ProbeOutcome("refused", 0, reason)
            if getattr(source, "last_json_from_cache", False):
                return _ProbeOutcome("empty", 0, "résultat en cache")
            return _ProbeOutcome("empty", 0, None)
        return _ProbeOutcome("found", len(candidates), None)
    except SourceFetchError as exc:
        return _ProbeOutcome("error", 0, public_source_message(exc.message or exc.code or ""))
    finally:
        close_source(source)


def _probe_links(
    db: Session,
    settings: Settings,
    name: str,
    media: Media,
    item: MediaItem,
    *,
    preference: list[str],
    profile,
) -> _ProbeOutcome:
    opened = remote_links_source(db, settings, name)
    if opened is None:
        return _ProbeOutcome("skipped", 0, "source non ouverte")
    provider_id, source = opened
    try:
        from datetime import datetime, timezone

        from mediatracker.core.source_binding import pick_bound_pages, seasons_needed
        from mediatracker.core.source_poll import _page_links, _search

        season_set: set[int | None]
        if media.type == "film":
            season_set = {None}
        else:
            season_set = {item.season} if item.season is not None else set()
        seasons = seasons_needed(media, season_set)
        bound = pick_bound_pages(
            db,
            settings,
            media,
            provider_id,
            seasons,
            now=datetime.now(timezone.utc),
            force_search=False,
            ignore_revalidation=True,
        )
        pages = bound
        if not pages:
            crit_quality = getattr(profile, "quality", None) or default_pref_quality(db)
            crit_language = getattr(profile, "language", None) or default_pref_language(db)
            results = _search(
                source,
                media,
                {season for season in seasons if season is not None},
                crit_quality,
                crit_language,
                False,
                db,
                settings,
                preferences=preferences_for(profile) if profile is not None else None,
            )
            if not results:
                return _ProbeOutcome("empty", 0, None)
            pages = [results[0]]
        page = pages[0]
        get_links = getattr(source, "get_links", None)
        if not callable(get_links):
            return _ProbeOutcome("empty", 0, None)
        if bound:
            raw = list(_page_links(source, page, profile))
        else:
            page_ref = page.ref if hasattr(page, "ref") else str(page)
            page_url = getattr(page, "url", None)
            raw = list(get_links(SourceRef(ref=page_ref, url=page_url)))
        count, reason = _count_hoster_links(raw, preference)
        if count > 0:
            return _ProbeOutcome("found", count, None)
        if reason:
            return _ProbeOutcome("refused", 0, reason)
        return _ProbeOutcome("empty", 0, None)
    except SourceFetchError as exc:
        return _ProbeOutcome("error", 0, public_source_message(exc.message or exc.code or ""))
    finally:
        close_source(source)
