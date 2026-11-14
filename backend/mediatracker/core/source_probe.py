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
    DEFAULT_DOWNLOAD_SOURCE_ORDER,
    hoster_allowed,
    parse_download_source_order,
    parse_hoster_preference,
)
from mediatracker.core.source_state import (
    _source_label,
    note_probe_source_health,
    public_source_message,
    record_source_probe,
)
from mediatracker.db.models import Media, MediaItem, Provider, QualityProfile
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.library.parse import slot_from_name
from mediatracker.providers.base import EpisodeQuery, SourceFetchError, SourcePreferences, SourceRef
from mediatracker.providers.sources.loading import close_source, episode_source, remote_links_source, source_base_url
from mediatracker.providers.sources.registry import for_type

_SLOT_TAIL = re.compile(r"(?i)(?:\s|\.|_|-)*(?:s\d{1,2}e\d{1,2}|saison\s+\d+\s+ep(?:isode)?\s+\d+)\s*$")


@dataclass(frozen=True)
class SourceProbeTarget:
    title: str
    media_type: str
    season: int | None
    episode: int | None
    imdb_id: str | None


@dataclass(frozen=True)
class SourceProbeRow:
    source: str
    label: str
    issue: str
    link_count: int
    reason: str | None
    duration_ms: int


@dataclass(frozen=True)
class _DeferredProbePersistence:
    type_name: str
    issue: str
    link_count: int
    duration_ms: int
    reason: str | None


def parse_probe_query(raw: str, *, imdb_id: str | None = None) -> SourceProbeTarget:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Indiquez un titre.")
    season, episode = slot_from_name(text)
    title = _SLOT_TAIL.sub("", text).strip() or text
    media_type = "series" if season is not None and episode is not None else "film"
    clean_imdb = (imdb_id or "").strip() or None
    return SourceProbeTarget(
        title=title,
        media_type=media_type,
        season=season,
        episode=episode,
        imdb_id=clean_imdb,
    )


def _effective_probe_order(db: Session, settings: Settings) -> list[str]:
    raw = _text_setting(db, "download_source_order", settings.download_source_order or DEFAULT_DOWNLOAD_SOURCE_ORDER)
    try:
        return parse_download_source_order(raw)
    except ValueError:
        return parse_download_source_order(DEFAULT_DOWNLOAD_SOURCE_ORDER)


def _source_disabled(db: Session, name: str, settings: Settings) -> str | None:
    from mediatracker.core.source_availability import source_unusable_reason

    blocked = source_unusable_reason(db, settings, name)
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
) -> list[SourceProbeRow]:
    """Interroge chaque source active de l'ordre global, sans lancer de téléchargement."""
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
    return rows


@dataclass(frozen=True)
class _ProbeOutcome:
    issue: str
    link_count: int
    reason: str | None


def _stub_media(target: SourceProbeTarget) -> Media:
    return Media(
        type=target.media_type,
        title=target.title,
        year=2020,
        followed=True,
        imdb_id=target.imdb_id,
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
        return _probe_episode(db, settings, name, media, item, spec=spec, comet_prefs=comet_prefs)
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


def _probe_episode(
    db: Session,
    settings: Settings,
    name: str,
    media: Media,
    item: MediaItem,
    *,
    spec,
    comet_prefs,
) -> _ProbeOutcome:
    if not media.imdb_id:
        return _ProbeOutcome("skipped", 0, "identifiant IMDb requis pour cette source")
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
            original_title=None,
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
            reason = getattr(source, "last_empty_reason", None)
            if reason:
                return _ProbeOutcome("refused", 0, reason)
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
    _provider_id, source = opened
    try:
        from mediatracker.core.source_poll import _search

        seasons = {item.season} if item.season is not None else set()
        crit_quality = getattr(profile, "quality", None) or default_pref_quality(db)
        crit_language = getattr(profile, "language", None) or default_pref_language(db)
        results = _search(
            source,
            media,
            seasons,
            crit_quality,
            crit_language,
            False,
            db,
            settings,
            preferences=preferences_for(profile) if profile is not None else None,
        )
        if not results:
            return _ProbeOutcome("empty", 0, None)
        page = results[0]
        get_links = getattr(source, "get_links", None)
        if not callable(get_links):
            return _ProbeOutcome("empty", 0, None)
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
