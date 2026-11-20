"""Followed media, episode rows, and the tracking figures the fiche will display."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.config import Settings, get_settings
from mediatracker.core.display_title import clean_display_title
from mediatracker.core.fiche_check import fiche_last_check_at, touch_fiche_checked
from mediatracker.core.file_status import FileStatusCounts, FileStatusIndex, aggregate_file_status
from mediatracker.core.jellyfin_web import jellyfin_base_url, jellyfin_web_url, playable_item_id
from mediatracker.core.polling import EpisodeAir, PollSubject, should_poll_source
from mediatracker.core.selection import LANGUAGES, QUALITIES
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.core.tracking import (
    EpisodeView,
    available_not_downloaded,
    progress_of,
    resolve_next_episode,
    tmdb_page_url,
)
from mediatracker.db.models import CatalogEntry, Download, Link, Media, MediaItem, MediaSeason, Setting
from mediatracker.integrations.tmdb import TmdbClient, TmdbError, TmdbTitle, borrow_tmdb
from mediatracker.settings_int import int_setting as _setting_int

router = APIRouter(prefix="/media", tags=["media"])
item_router = APIRouter(prefix="/items", tags=["media"])

SERIES_STATUSES = frozenset({"returning", "ended", "canceled", "planned", "in_production", "unknown"})
POLICIES = frozenset({"auto", "propose", "off"})
# Same set as the dashboard: a queued, failed, or finished download is not offered again.
_DOWNLOAD_BLOCKS_OFFER = ("pending", "resolving", "queued", "downloading", "post_processing", "failed", "done")


def _media_fallback_profile_ids_out(row: Media) -> list[int] | None:
    from mediatracker.core.profile_fallback import fallback_profile_ids_for_api

    return fallback_profile_ids_for_api(row)


class MediaCreate(BaseModel):
    type: str | None = Field(default=None, pattern="^(film|series)$")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    year: int | None = Field(default=None, ge=1880, le=2100)
    tmdb_id: int | None = Field(default=None, ge=1)
    tmdb_url: str | None = Field(default=None, max_length=500)
    overview: str | None = None
    poster_url: str | None = None
    series_status: str | None = None
    on_hold: bool = False
    new_episode_policy: str | None = None
    pref_quality: str | None = None
    pref_language: str | None = None
    next_episode_season: int | None = Field(default=None, ge=0)
    next_episode_number: int | None = Field(default=None, ge=0)
    next_episode_title: str | None = None
    next_air_date: date | None = None
    seasons: list[int] | None = None
    start_now: bool = False


class MediaFollowIn(BaseModel):
    new_episode_policy: str | None = None
    seasons: list[int] | None = None
    start_now: bool = False
    on_hold: bool = False


class MediaPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    year: int | None = None
    tmdb_id: int | None = None
    tmdb_id_confirm: str | None = Field(default=None, pattern="^(keep_episodes|rebuild_episodes)$")
    overview: str | None = None
    poster_url: str | None = None
    followed: bool | None = None
    on_hold: bool | None = None
    series_status: str | None = None
    new_episode_policy: str | None = None
    pref_quality: str | None = None
    pref_language: str | None = None
    allow_fallback: bool | None = None
    fallback_profile_ids: list[int] | None = None
    source_order: str | None = None
    quality_profile_id: int | None = None
    scan_interval_min: int | None = Field(default=None, ge=60, le=10080)
    keep: bool | None = None
    auto_delete: bool | None = None
    auto_delete_days: int | None = Field(default=None, ge=1, le=365)
    next_episode_season: int | None = None
    next_episode_number: int | None = None
    next_episode_title: str | None = None
    next_air_date: date | None = None


class UnfollowIn(BaseModel):
    trash: bool = False


class NextEpisodeOut(BaseModel):
    season: int | None
    episode: int | None
    title: str | None
    air_date: date | None


class ProgressOut(BaseModel):
    watched: int
    total: int


class PollOut(BaseModel):
    allowed: bool
    reason: str
    last_check_at: datetime | None = None
    next_check_at: datetime | None = None
    last_result: str | None = None
    last_message: str | None = None
    interval_min: int = 720


class CheckOut(BaseModel):
    result: str
    message: str
    episodes: int
    queued: int
    proposed: int
    last_check_at: datetime
    next_check_at: datetime | None = None


class VersionOut(BaseModel):
    title: str
    season: int | None = None
    quality: str | None = None
    language: str | None = None
    kept: bool = False
    reason: str = ""


class MediaOut(BaseModel):
    id: int
    type: str
    title: str
    year: int | None
    tmdb_id: int | None
    tmdb_url: str | None
    overview: str | None
    poster_url: str | None
    backdrop_url: str | None
    followed: bool
    on_hold: bool
    follow_origin: str = "user"
    availability: str = "waiting"
    watched: bool = False
    on_disk: bool = False
    disk_label: str | None = None
    attached_count: int = 0
    source_versions: list[VersionOut] = Field(default_factory=list)
    series_status: str | None
    new_episode_policy: str
    auto_download: bool
    pref_quality: str
    pref_language: str
    allow_fallback: bool
    fallback_profile_ids: list[int] | None = None
    source_order: str | None = None
    quality_profile_id: int | None = None
    scan_interval_min: int | None
    keep: bool
    auto_delete: bool
    auto_delete_days: int | None
    cleanup_code: str | None = None
    cleanup_reason: str | None = None
    next_episode: NextEpisodeOut | None
    progress: ProgressOut
    available_not_downloaded: int
    poll: PollOut
    auto_status: str
    created_at: datetime
    jellyfin_web_url: str | None = None
    catalog_id: int | None = None
    source_url: str | None = None
    identity_edited_at: datetime | None = None
    file_status: str = "absent"
    file_absent: bool = False
    film_not_yet_available: bool = False
    film_status_line: str | None = None
    last_repair_at: datetime | None = None
    last_repair_summary: str | None = None
    tmdb_identify_status: str | None = None
    tmdb_identify_candidates: list[dict] | None = None
    tmdb_refresh_warning: str | None = None
    tmdb_vote_average: float | None = None
    tmdb_vote_count: int | None = None
    tmdb_genres: list[str] | None = None
    tmdb_runtime_min: int | None = None
    tmdb_original_language: str | None = None
    tmdb_collection_id: int | None = None
    tmdb_collection_name: str | None = None
    tmdb_watch_providers_fr: list[str] | None = None
    tmdb_release_by_region: dict[str, dict[str, str | None]] | None = None
    tmdb_trailer_youtube_key: str | None = None
    tmdb_trailer_kind: str | None = None
    tmdb_trailer_language: str | None = None


class MediaPage(BaseModel):
    items: list[MediaOut]
    next_cursor: str | None = None


class IdentityChangeOut(BaseModel):
    id: int
    source: str
    old_tmdb: int | None = None
    new_tmdb: int | None = None
    old_title: str
    new_title: str
    old_year: int | None = None
    new_year: int | None = None
    changed_at: datetime
    undone_at: datetime | None = None


class EpisodeIn(BaseModel):
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    title: str | None = None
    air_date: date | None = None
    downloaded: bool = False
    watched: bool = False
    source_available: bool = False
    keep: bool = False


class EpisodePatch(BaseModel):
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    title: str | None = None
    air_date: date | None = None
    downloaded: bool | None = None
    watched: bool | None = None
    source_available: bool | None = None
    keep: bool | None = None


class ServedProfileOut(BaseModel):
    profile_id: int
    profile_name: str
    repli: bool = True


class ProfileReplacementProposalOut(BaseModel):
    id: int
    principal_profile_id: int
    principal_profile_name: str
    repli_profile_id: int
    repli_profile_name: str


class EpisodeOut(BaseModel):
    id: int
    season: int | None
    episode: int | None
    title: str | None
    air_date: date | None
    state: str
    downloaded: bool
    downloaded_at: datetime | None
    watched: bool
    watched_at: datetime | None
    watch_source: str = "auto"
    source_available: bool
    download_active: bool = False
    download_busy: bool = False
    keep: bool
    filename: str | None = None
    original_name: str | None = None
    file_absent: bool = False
    file_status: str = "absent"
    trash_entry_id: int | None = None
    served_profile: ServedProfileOut | None = None
    profile_replacement: ProfileReplacementProposalOut | None = None


class FileStatusCountsOut(BaseModel):
    present: int = 0
    trash: int = 0
    absent: int = 0
    downloading: int = 0


class SeasonOut(BaseModel):
    number: int | None
    name: str | None = None
    air_date: date | None = None
    episode_count: int | None = None
    keep: bool = False
    file_status: str = "absent"
    file_status_counts: FileStatusCountsOut = Field(default_factory=FileStatusCountsOut)
    episodes: list[EpisodeOut]


class SeasonPatch(BaseModel):
    keep: bool


class EpisodeListOut(BaseModel):
    seasons: list[SeasonOut]


class ReanalyzeOut(BaseModel):
    found: int
    new: int
    absent: int


def _cursor(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", "Curseur invalide.") from exc
    if parsed < 0:
        raise Problem(422, "Requête invalide", "Curseur invalide.")
    return parsed


def _check_status(media_type: str, status: str | None) -> None:
    if status is None:
        return
    if media_type != "series":
        raise Problem(422, "Requête invalide", "Le statut de série ne s'applique pas à un film.")
    if status not in SERIES_STATUSES:
        raise Problem(422, "Requête invalide", "Statut de série inconnu.")


def _default_policy(db: Session) -> str:
    from mediatracker.core.follow import default_policy

    return default_policy(db)


def _auto_status(
    row: Media,
    episodes: list[MediaItem],
    db: Session,
    download_states: dict[int, list[str]] | None = None,
) -> str:
    if not row.followed:
        return "Suivi arrêté. Les fichiers et l'historique « vu » sont conservés."
    if row.on_hold:
        return "En pause : pas de recherche, pas de téléchargement, pas de notification."
    if row.new_episode_policy != "auto":
        return "Téléchargement automatique désactivé. Les liens trouvés sont proposés."
    if row.type in {"film", "series"} and _media_watched(row, episodes):
        return "Vu."
    if row.type == "film":
        film = next((item for item in episodes if item.season is None and item.episode is None), None)
        if film is not None and (film.downloaded or film.file_path):
            return "Obtenu"
    states: list[str] = []
    if download_states is None:
        ids = [item.id for item in episodes]
        if ids:
            states = list(
                db.scalars(
                    select(Download.state).where(
                        Download.media_item_id.in_(ids),
                        Download.state.not_in(("cancelled",)),
                    )
                ).all()
            )
    else:
        for item in episodes:
            states.extend(download_states.get(item.id, []))
    if any(state in {"downloading", "post_processing", "resolving"} for state in states):
        phase = "en cours"
    elif any(state in {"pending", "queued"} for state in states):
        phase = "en file"
    elif row.last_poll_result == "nothing":
        phase = "aucun lien trouvé"
    elif any(state == "done" for state in states) or any(item.downloaded for item in episodes):
        phase = "terminé"
    else:
        phase = "en attente"
    when = ""
    nxt = row.next_source_poll_at
    if nxt is not None:
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        from zoneinfo import ZoneInfo

        zone_name = _setting_text(db, "notify_timezone", "Europe/Paris")
        try:
            zone = ZoneInfo(zone_name)
        except Exception:
            zone = ZoneInfo("Europe/Paris")
        when = f" – prochaine recherche à {nxt.astimezone(zone).strftime('%H:%M')}"
    return f"Téléchargement automatique : {phase}{when}"


def _check_policy(policy: str) -> None:
    from mediatracker.core.follow import FollowError, check_policy

    try:
        check_policy(policy)
    except FollowError as exc:
        raise Problem(exc.status, exc.title, exc.detail) from exc


def _views(rows: list[MediaItem]) -> list[EpisodeView]:
    return [
        EpisodeView(
            season=row.season,
            episode=row.episode,
            title=row.title,
            air_date=row.air_date,
            downloaded=row.downloaded,
            watched=row.watched,
            source_available=row.source_available,
        )
        for row in rows
    ]


def _catalog_links(db: Session, rows: list[Media], settings) -> dict[int, tuple[int, str | None]]:
    from mediatracker.core.catalog_source_visibility import visible_latest_source_types

    ids = {row.tmdb_id for row in rows if row.tmdb_id}
    if not ids:
        return {}
    visible_sources = {source.strip().lower() for source in visible_latest_source_types(db, settings)}
    entries = [
        row
        for row in db.scalars(select(CatalogEntry).where(CatalogEntry.tmdb_id.in_(ids))).all()
        if (row.source or "").strip().lower() in visible_sources
    ]
    by_key: dict[tuple[str, int], tuple[int, str | None]] = {}
    for entry in entries:
        if entry.tmdb_id is None:
            continue
        by_key.setdefault((entry.media_type, entry.tmdb_id), (entry.id, entry.source_url))
    found: dict[int, tuple[int, str | None]] = {}
    for row in rows:
        if row.tmdb_id is None:
            continue
        hit = by_key.get((row.type, row.tmdb_id))
        if hit is not None:
            found[row.id] = hit
    return found


def _counts_out(counts: FileStatusCounts) -> FileStatusCountsOut:
    return FileStatusCountsOut(
        present=counts.present,
        trash=counts.trash,
        absent=counts.absent,
        downloading=counts.downloading,
    )


def _film_file_status(
    row: Media,
    episodes: list[MediaItem],
    db: Session,
    index: FileStatusIndex | None,
) -> str:
    if row.type == "film":
        film = next((item for item in episodes if item.season is None and item.episode is None), None)
        if film is None:
            return "absent"
        if index is None:
            index = FileStatusIndex.load(db, [film])
        return index.for_item(film)
    if not episodes:
        return "absent"
    if index is None:
        index = FileStatusIndex.load(db, episodes)
    return aggregate_file_status(index.counts_for(episodes))


def _to_media(
    row: Media,
    episodes: list[MediaItem],
    db: Session,
    settings=None,
    *,
    poll_defaults: tuple[int, bool, int] | None = None,
    download_states: dict[int, list[str]] | None = None,
    catalog_index: dict[int, tuple[int, str | None]] | None = None,
    file_status_index: FileStatusIndex | None = None,
    jellyfin_base: str | None = None,
    explain_ctx=None,
) -> MediaOut:
    views = _views(episodes)
    progress = progress_of(views)
    nxt = resolve_next_episode(
        media_type=row.type,
        season=row.next_episode_season,
        episode=row.next_episode_number,
        title=row.next_episode_title,
        air_date=row.next_air_date,
        episodes=views,
    )
    if catalog_index is None:
        if settings is None:
            catalog_index = {}
        else:
            catalog_index = _catalog_links(db, [row], settings)
    catalog_id, source_url = catalog_index.get(row.id, (None, None))
    cleanup_code, cleanup_reason = _cleanup_fields(db, settings, row, episodes, explain_ctx=explain_ctx)
    availability, disk_label = _availability(row, episodes)
    from mediatracker.core.film_availability import film_availability_label, film_not_yet_available

    resolved_file_status = _film_file_status(row, episodes, db, file_status_index)
    waiting_film = row.type == "film" and film_not_yet_available(
        row,
        episodes,
        file_present=resolved_file_status == "present",
    )
    film_status = film_availability_label(row, episodes) if waiting_film else None
    from mediatracker.core.forget import is_file_absent

    film_file_absent = False
    if row.type == "film":
        for item in episodes:
            if is_file_absent(item):
                film_file_absent = True
                break
    return MediaOut(
        id=row.id,
        type=row.type,
        title=clean_display_title(row.title) or row.title,
        year=row.year,
        tmdb_id=row.tmdb_id,
        tmdb_url=tmdb_page_url(row.type, row.tmdb_id),
        overview=row.overview,
        poster_url=row.poster_url,
        backdrop_url=row.backdrop_url,
        followed=row.followed,
        on_hold=row.on_hold,
        follow_origin=row.follow_origin or "user",
        availability=availability,
        watched=_media_watched(row, episodes),
        on_disk=_media_on_disk(row, episodes),
        disk_label=disk_label,
        attached_count=int(getattr(row, "_attached", 0) or 0),
        source_versions=_versions(row),
        series_status=row.series_status,
        new_episode_policy=row.new_episode_policy,
        auto_download=row.auto_download,
        pref_quality=row.pref_quality,
        pref_language=row.pref_language,
        allow_fallback=row.allow_fallback,
        fallback_profile_ids=_media_fallback_profile_ids_out(row),
        source_order=row.source_order,
        quality_profile_id=row.quality_profile_id,
        scan_interval_min=row.scan_interval_min,
        keep=row.keep,
        auto_delete=row.auto_delete,
        auto_delete_days=row.auto_delete_days,
        cleanup_code=cleanup_code,
        cleanup_reason=cleanup_reason,
        next_episode=None
        if nxt is None
        else NextEpisodeOut(
            season=nxt.season,
            episode=nxt.episode,
            title=clean_display_title(nxt.title) or nxt.title,
            air_date=nxt.air_date,
        ),
        progress=ProgressOut(watched=progress.watched, total=progress.total),
        available_not_downloaded=available_not_downloaded(views),
        poll=_poll(row, episodes, db, poll_defaults=poll_defaults),
        auto_status=_auto_status(row, episodes, db, download_states),
        created_at=row.created_at,
        jellyfin_web_url=jellyfin_web_url(
            jellyfin_base if jellyfin_base is not None else jellyfin_base_url(db),
            playable_item_id(row, episodes),
        ),
        catalog_id=catalog_id,
        source_url=source_url,
        identity_edited_at=row.identity_edited_at,
        file_status=resolved_file_status,
        file_absent=film_file_absent,
        film_not_yet_available=waiting_film,
        film_status_line=film_status,
        last_repair_at=row.last_repair_at,
        last_repair_summary=row.last_repair_summary,
        tmdb_identify_status=row.tmdb_identify_status,
        tmdb_identify_candidates=row.tmdb_identify_candidates,
        tmdb_refresh_warning=getattr(row, "_tmdb_refresh_warning", None),
        tmdb_vote_average=row.tmdb_vote_average,
        tmdb_vote_count=row.tmdb_vote_count,
        tmdb_genres=row.tmdb_genres,
        tmdb_runtime_min=row.tmdb_runtime_min,
        tmdb_original_language=row.tmdb_original_language,
        tmdb_collection_id=row.tmdb_collection_id,
        tmdb_collection_name=row.tmdb_collection_name,
        tmdb_watch_providers_fr=row.tmdb_watch_providers_fr,
        tmdb_release_by_region=row.tmdb_release_by_region,
        tmdb_trailer_youtube_key=row.tmdb_trailer_youtube_key,
        tmdb_trailer_kind=row.tmdb_trailer_kind,
        tmdb_trailer_language=row.tmdb_trailer_language,
    )


def _cleanup_fields(
    db: Session,
    settings,
    row: Media,
    episodes: list[MediaItem],
    *,
    explain_ctx=None,
) -> tuple[str | None, str | None]:
    if settings is None or row.type not in {"film", "series"}:
        return None, None
    from mediatracker.core.cleanup import explain_media

    hold = explain_media(db, settings, row, episodes, datetime.now(timezone.utc), ctx=explain_ctx)
    if hold is None:
        return None, None
    return hold.code, hold.message


def _availability(row: Media, episodes: list[MediaItem]) -> tuple[str, str | None]:
    if row.on_hold:
        return "paused", None
    if row.type == "film":
        film = next((item for item in episodes if item.season is None), None)
        if film is not None and (film.downloaded or film.file_path):
            return "on_disk", None
        if film is not None and film.watched:
            return "waiting", None
        from mediatracker.core.film_availability import film_not_yet_available

        if film_not_yet_available(row, episodes):
            return "unavailable", None
        if film is not None and film.source_available:
            return "available", None
        return "waiting", None
    seasons: dict[int, list[int]] = {}
    for item in episodes:
        if item.season is None:
            continue
        seasons.setdefault(item.season, [0, 0, 0])
        seasons[item.season][0] += 1
        if item.downloaded or item.file_path:
            seasons[item.season][1] += 1
        elif item.source_available:
            seasons[item.season][2] += 1
    if not seasons:
        return "waiting", None
    any_disk = any(disk for _total, disk, _ready in seasons.values())
    all_disk = all(disk == total and total > 0 for total, disk, _ready in seasons.values())
    any_ready = any(ready for _total, _disk, ready in seasons.values())
    label = None
    for number, (total, disk, ready) in sorted(seasons.items()):
        if 0 < disk < total or (ready and disk):
            label = f"S{number:02d} : {disk}/{total} dispo"
            break
    if all_disk and any_disk:
        return "on_disk", None
    if label:
        return "partial", label
    if any_ready:
        return "available", None
    if any_disk:
        return "on_disk", None
    return "waiting", None


def _film_slot(episodes: list[MediaItem]) -> MediaItem | None:
    return next((item for item in episodes if item.season is None), None)


def _media_watched(row: Media, episodes: list[MediaItem]) -> bool:
    if row.type == "film":
        film = _film_slot(episodes)
        return film is not None and film.watched
    if row.type == "series":
        from mediatracker.core.series_follow import series_all_aired_episodes_watched

        return series_all_aired_episodes_watched(episodes)
    return False


def _media_on_disk(row: Media, episodes: list[MediaItem]) -> bool:
    if row.type == "film":
        film = _film_slot(episodes)
        return film is not None and (film.downloaded or bool(film.file_path))
    for item in episodes:
        if item.season is None:
            continue
        if item.downloaded or item.file_path:
            return True
    return False


def _versions(row: Media) -> list[VersionOut]:
    raw = row.source_report or []
    versions: list[VersionOut] = []
    if not isinstance(raw, list):
        return versions
    for item in raw:
        if not isinstance(item, dict):
            continue
        versions.append(
            VersionOut(
                title=clean_display_title(str(item.get("title") or row.title)) or str(item.get("title") or row.title),
                season=item.get("season") if isinstance(item.get("season"), int) else None,
                quality=item.get("quality") if isinstance(item.get("quality"), str) else None,
                language=item.get("language") if isinstance(item.get("language"), str) else None,
                kept=bool(item.get("kept")),
                reason=str(item.get("reason") or ""),
            )
        )
    return versions


def _poll(
    row: Media,
    episodes: list[MediaItem],
    db: Session,
    *,
    poll_defaults: tuple[int, bool, int] | None = None,
) -> PollOut:
    subject = PollSubject(
        followed=row.followed,
        on_hold=row.on_hold,
        media_type=row.type,
        series_status=row.series_status,
        new_episode_policy=row.new_episode_policy,
        last_source_poll_at=row.last_source_poll_at,
        next_source_poll_at=row.next_source_poll_at,
        next_air_date=row.next_air_date,
        episodes=tuple(
            EpisodeAir(
                item.air_date,
                bool(item.downloaded or (row.type == "film" and item.file_path)),
                on_disk=bool(row.type == "film" and (item.downloaded or item.file_path)),
                watched=bool(item.watched),
                season=item.season,
            )
            for item in episodes
        ),
        catch_up=bool(row.catch_up_pending),
        tracked_seasons=(
            None if row.tracked_seasons is None else tuple(int(number) for number in row.tracked_seasons)
        ),
    )
    if poll_defaults is None:
        defaults = get_settings()
        interval_min = _setting_int(db, "scan_interval_min", defaults.scan_interval_min)
        ongoing_only = _setting_bool(db, "scan_ongoing_only", defaults.scan_ongoing_only)
        window_days = _setting_int(db, "scan_air_window_days", defaults.scan_air_window_days)
    else:
        interval_min, ongoing_only, window_days = poll_defaults
    allowed, reason = should_poll_source(
        subject,
        now=datetime.now(timezone.utc),
        interval=timedelta(minutes=interval_min),
        ongoing_only=ongoing_only,
        window_days=window_days,
    )
    minutes = row.scan_interval_min or interval_min
    return PollOut(
        allowed=allowed,
        reason=reason,
        last_check_at=fiche_last_check_at(row),
        next_check_at=row.next_source_poll_at,
        last_result=row.last_poll_result,
        last_message=row.last_poll_message,
        interval_min=minutes,
    )


def _download_states(db: Session, item_ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    if not item_ids:
        return found
    rows = db.execute(
        select(Download.media_item_id, Download.state).where(
            Download.media_item_id.in_(item_ids),
            Download.state.not_in(("cancelled",)),
        )
    ).all()
    for item_id, state in rows:
        found.setdefault(item_id, []).append(state)
    return found


def _setting_text(db: Session, key: str, default: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    return row.value


def _setting_bool(db: Session, key: str, default: bool) -> bool:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    return row.value == "true"



def _load_items(db: Session, media_ids: list[int]) -> dict[int, list[MediaItem]]:
    grouped: dict[int, list[MediaItem]] = {media_id: [] for media_id in media_ids}
    if not media_ids:
        return grouped
    rows = db.scalars(select(MediaItem).where(MediaItem.media_id.in_(media_ids))).all()
    for row in rows:
        grouped.setdefault(row.media_id, []).append(row)
    return grouped


def _get_media(db: Session, media_id: int) -> Media:
    row = db.get(Media, media_id)
    if row is None:
        raise Problem(404, "Introuvable", "Média introuvable.")
    return row


def _item_ids_in(db: Session, item_ids: list[int], states: tuple[str, ...]) -> set[int]:
    if not item_ids:
        return set()
    return set(
        db.scalars(
            select(Download.media_item_id).where(
                Download.media_item_id.in_(item_ids),
                Download.state.in_(states),
            )
        ).all()
    )


def _busy_item_ids(db: Session, item_ids: list[int]) -> set[int]:
    return _item_ids_in(db, item_ids, _DOWNLOAD_BLOCKS_OFFER)


def _in_progress_ids(db: Session, item_ids: list[int]) -> set[int]:
    from mediatracker.core.force_download import IN_PROGRESS

    return _item_ids_in(db, item_ids, IN_PROGRESS)


def _download_activity_ids(db: Session, item_ids: list[int]) -> tuple[set[int], set[int]]:
    if not item_ids:
        return set(), set()
    from mediatracker.core.force_download import IN_PROGRESS

    states = tuple(set(_DOWNLOAD_BLOCKS_OFFER) | set(IN_PROGRESS))
    busy: set[int] = set()
    progress: set[int] = set()
    rows = db.execute(
        select(Download.media_item_id, Download.state).where(
            Download.media_item_id.in_(item_ids),
            Download.state.in_(states),
        )
    )
    for item_id, state in rows:
        if state in _DOWNLOAD_BLOCKS_OFFER:
            busy.add(item_id)
        if state in IN_PROGRESS:
            progress.add(item_id)
    return busy, progress


def _sync_library_claims(db: Session, request: Request, media: Media, items: list[MediaItem]) -> None:
    """Attach missing series files from the show folder, then drop a gone video flag. Does not queue."""
    from mediatracker.core.source_poll import refresh_library_claims
    from mediatracker.library.series_folder_attach import attach_series_folder_if_needed

    settings = request.app.state.settings
    now = datetime.now(timezone.utc)
    changed = bool(attach_series_folder_if_needed(db, settings, media, items, now))
    if refresh_library_claims(db, media, items, settings, now):
        changed = True
    if changed:
        db.commit()


def _episode_out(
    row: MediaItem,
    *,
    download_active: bool = False,
    download_busy: bool = False,
    file_status_index: FileStatusIndex | None = None,
    file_status: str | None = None,
    db: Session | None = None,
    settings: Settings | None = None,
    media: Media | None = None,
    episode_profile_index=None,
) -> EpisodeOut:
    from mediatracker.core.forget import is_file_absent

    resolved = file_status
    if resolved is None:
        if file_status_index is None:
            resolved = "absent"
        else:
            resolved = file_status_index.for_item(row)

    trash_entry_id = None
    if file_status_index is not None and row.id is not None:
        trash_entry_id = file_status_index.trash_entry_for(row.id)

    served_profile = None
    profile_replacement = None
    if row.id is not None and (
        episode_profile_index is not None
        or (db is not None and settings is not None and media is not None)
    ):
        from mediatracker.core.profile_replacement import (
            active_proposal_for_item,
            proposal_to_out,
            served_profile_display,
        )

        if episode_profile_index is not None:
            served_raw = episode_profile_index.served_display_for(row)
            proposal = episode_profile_index.active_proposal(row.id)
            if proposal is not None:
                out = episode_profile_index.proposal_to_out(proposal)
                profile_replacement = ProfileReplacementProposalOut(
                    id=out.id,
                    principal_profile_id=out.principal_profile_id,
                    principal_profile_name=out.principal_profile_name,
                    repli_profile_id=out.repli_profile_id,
                    repli_profile_name=out.repli_profile_name,
                )
        else:
            served_raw = served_profile_display(db, media, row, settings)
            proposal = active_proposal_for_item(db, row.id)
            if proposal is not None:
                out = proposal_to_out(db, proposal)
                profile_replacement = ProfileReplacementProposalOut(
                    id=out.id,
                    principal_profile_id=out.principal_profile_id,
                    principal_profile_name=out.principal_profile_name,
                    repli_profile_id=out.repli_profile_id,
                    repli_profile_name=out.repli_profile_name,
                )
        if served_raw is not None:
            served_profile = ServedProfileOut(**served_raw)

    return EpisodeOut(
        id=row.id,
        season=row.season,
        episode=row.episode,
        title=clean_display_title(row.title) or row.title,
        air_date=row.air_date,
        state=row.state,
        downloaded=row.downloaded,
        downloaded_at=row.downloaded_at,
        watched=row.watched,
        watched_at=row.watched_at,
        watch_source=row.watch_source or "auto",
        source_available=row.source_available,
        download_active=download_active,
        download_busy=download_busy,
        keep=row.keep,
        filename=_filename(row.file_path),
        original_name=row.original_name,
        file_absent=is_file_absent(row),
        file_status=resolved,
        trash_entry_id=trash_entry_id,
        served_profile=served_profile,
        profile_replacement=profile_replacement,
    )


def _filename(path: str | None) -> str | None:
    if not path:
        return None
    name = Path(path).name
    return name or None


def _apply_flags(row: MediaItem, *, downloaded: bool | None = None, watched: bool | None = None) -> None:
    now = datetime.now(timezone.utc)
    if downloaded is not None:
        row.downloaded = downloaded
        if downloaded and row.downloaded_at is None:
            row.downloaded_at = now
        if not downloaded:
            row.downloaded_at = None
        if downloaded and row.state in {"missing", "available"}:
            row.state = "downloaded"
    if watched is not None:
        row.watched = watched
        row.watch_source = "manual" if watched else "auto"
        if watched and row.watched_at is None:
            row.watched_at = now
        if not watched:
            row.watched_at = None
        if watched:
            row.state = "watched"


@router.get("", response_model=MediaPage)
def list_media(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    origin: str | None = None,
    identify_status: str | None = None,
) -> MediaPage:
    del _principal
    stmt = select(Media)
    if origin == "identification":
        stmt = stmt.where(Media.follow_origin == "identification")
    if identify_status:
        stmt = stmt.where(Media.tmdb_identify_status == identify_status)
    if cursor is not None:
        stmt = stmt.where(Media.id > _cursor(cursor))
    rows = list(db.scalars(stmt.order_by(Media.id).limit(limit + 1)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    grouped = _load_items(db, [row.id for row in rows])
    defaults = get_settings()
    poll_defaults = (
        _setting_int(db, "scan_interval_min", defaults.scan_interval_min),
        _setting_bool(db, "scan_ongoing_only", defaults.scan_ongoing_only),
        _setting_int(db, "scan_air_window_days", defaults.scan_air_window_days),
    )
    item_ids = [item.id for episodes in grouped.values() for item in episodes]
    all_items = [item for episodes in grouped.values() for item in episodes]
    download_states = _download_states(db, item_ids)
    catalog_index = _catalog_links(db, rows, request.app.state.settings)
    file_status_index = FileStatusIndex.load(db, all_items)
    from mediatracker.core.cleanup import load_media_explain_context

    jellyfin_base = jellyfin_base_url(db)
    explain_ctx = load_media_explain_context(db, request.app.state.settings, [row.id for row in rows])
    return MediaPage(
        items=[
            _to_media(
                row,
                grouped.get(row.id, []),
                db,
                request.app.state.settings,
                poll_defaults=poll_defaults,
                download_states=download_states,
                catalog_index=catalog_index,
                file_status_index=file_status_index,
                jellyfin_base=jellyfin_base,
                explain_ctx=explain_ctx,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


def _follow_input(body: MediaCreate):
    from mediatracker.core.follow import FollowInput

    return FollowInput(**body.model_dump())


def insert_followed(
    db: Session,
    secret_key: str,
    body: MediaCreate,
    *,
    settings: Settings | None = None,
) -> tuple[Media, list[MediaItem]]:
    """Create a followed media the same way ``POST /media`` does."""
    from mediatracker.core.follow import FollowError, follow

    try:
        return follow(db, secret_key, _follow_input(body), source="create", settings=settings)
    except FollowError as exc:
        raise Problem(exc.status, exc.title, exc.detail) from exc


@router.post("", response_model=MediaOut, status_code=201)
def create_media(
    body: MediaCreate,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> MediaOut:
    del _principal
    settings = request.app.state.settings
    row, episodes = insert_followed(db, settings.secret_key, body, settings=settings)
    from mediatracker.core.attach_deferred import commit_follow_side_effects, schedule_attach_known_files

    commit_follow_side_effects(db, settings)
    schedule_attach_known_files(request.app.state.session_factory, settings, row.id)
    row._attached = 0  # type: ignore[attr-defined]
    file_status_index = FileStatusIndex.load(db, episodes)
    return _to_media(row, episodes, db, settings, file_status_index=file_status_index)


def _store_tmdb(db: Session, row: Media, fetched: TmdbTitle) -> list[MediaItem]:
    from mediatracker.integrations.tmdb_media import store_tmdb_episodes

    return store_tmdb_episodes(db, row, fetched)


def _fill_next_episode(row: Media, episodes: list[MediaItem]) -> None:
    from mediatracker.integrations.tmdb_media import fill_next_episode

    fill_next_episode(row, episodes)


def _fetch_tmdb_title(db: Session, secret_key: str, tmdb_id: int, media_type: str) -> tuple[TmdbTitle, str | None]:
    token = tmdb_token.read_tmdb_token(db, secret_key)
    if not token:
        raise Problem(422, "TMDB", "Jeton TMDB absent : métadonnées non récupérées.")
    from mediatracker.core.tmdb.catalog_settings import read_tmdb_catalog_settings

    catalog = read_tmdb_catalog_settings(db)
    client, owned = borrow_tmdb(token, TmdbClient, language=catalog.display_language)
    try:
        fetched = client.fetch(tmdb_id, media_type)
        imdb_id = fetched.imdb_id
        if imdb_id is None:
            lookup = getattr(client, "external_imdb", None)
            if lookup is not None:
                imdb_id = lookup(tmdb_id, media_type)
    except TmdbError as exc:
        status = 404 if exc.code == "NOT_FOUND" else 422 if exc.code == "UNAUTHORIZED" else 502
        raise Problem(status, "TMDB", exc.message) from exc
    finally:
        if owned:
            client.close()
    if fetched.media_type != media_type:
        raise Problem(422, "TMDB", "Cet identifiant TMDB ne correspond pas au type de la fiche.")
    return fetched, imdb_id


def _apply_fetched_tmdb(
    db: Session,
    row: Media,
    fetched: TmdbTitle,
    *,
    imdb_id: str | None,
    moment: datetime,
) -> list[MediaItem]:
    from mediatracker.core.tmdb.apply import apply_tmdb

    return apply_tmdb(db, row, fetched, imdb_id=imdb_id, moment=moment)


@router.post("/{media_id}/follow", response_model=MediaOut)
def follow_existing_media(
    media_id: int,
    body: MediaFollowIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> MediaOut:
    """Follow an existing fiche without creating a duplicate."""
    del _principal
    row = _get_media(db, media_id)
    from mediatracker.core.follow import FollowError, FollowInput, follow

    try:
        follow(
            db,
            None,
            FollowInput(
                type=row.type,
                title=row.title,
                new_episode_policy=body.new_episode_policy,
                seasons=body.seasons,
                start_now=body.start_now,
                on_hold=body.on_hold,
            ),
            media=row,
            source="existing",
            settings=request.app.state.settings,
        )
    except FollowError as exc:
        raise Problem(exc.status, exc.title, exc.detail) from exc
    settings = request.app.state.settings
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
    from mediatracker.library.series_folder_attach import attach_series_folder_if_needed

    attach_series_folder_if_needed(
        db,
        settings,
        row,
        items,
        datetime.now(timezone.utc),
        force=True,
    )
    from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker

    wake_worker(db, settings, REASON_API_MEDIA, commit=False)
    db.commit()
    db.refresh(row)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
    file_status_index = FileStatusIndex.load(db, items)
    return _to_media(row, items, db, request.app.state.settings, file_status_index=file_status_index)


@router.post("/{media_id}/check", response_model=CheckOut)
def check_media(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> CheckOut:
    """Run one source pass now. The schedule (interval, air window) does not block it."""
    del _principal
    row = _get_media(db, media_id)
    if not row.followed:
        raise Problem(409, "Conflit", "Ce média n'est pas suivi.")
    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll

    settings = request.app.state.settings
    from mediatracker.core.source_availability import (
        DOWNLOAD_BLOCKED_CODE,
        DOWNLOAD_BLOCKED_MESSAGE,
        any_download_source_available,
    )

    if not any_download_source_available(db, settings):
        raise Problem(
            422,
            "Sources indisponibles",
            DOWNLOAD_BLOCKED_MESSAGE,
            extra={"code": DOWNLOAD_BLOCKED_CODE},
        )
    report = orchestrate_profile_first_poll(
        request.app.state.session_factory,
        settings,
        row.id,
        force=True,
    )
    if report is None:
        from mediatracker.core.source_poll import PollReport

        report = PollReport(
            result="nothing",
            message="Aucune source configurée.",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=row.last_source_poll_at or datetime.now(timezone.utc),
            next_check_at=row.next_source_poll_at,
        )
    from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker
    from mediatracker.db.session import commit_retry

    wake_worker(db, request.app.state.settings, REASON_API_MEDIA, commit=False)
    commit_retry(db)
    return CheckOut(
        result=report.result,
        message=report.message,
        episodes=report.episodes,
        queued=report.queued,
        proposed=report.proposed,
        last_check_at=report.last_check_at,
        next_check_at=report.next_check_at,
    )


class ForceDownloadIn(BaseModel):
    item_id: int | None = Field(default=None, ge=1)
    season: int | None = Field(default=None, ge=0)
    confirm: bool = False


class ForceLocalOut(BaseModel):
    item_id: int
    season: int | None
    episode: int | None
    path: str


class ForceDownloadOut(BaseModel):
    queued: int
    skipped_busy: int
    needs_confirm: bool
    local: list[ForceLocalOut]
    message: str


@router.post("/{media_id}/force-download", response_model=ForceDownloadOut)
def force_download(
    media_id: int,
    body: ForceDownloadIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ForceDownloadOut:
    """Queue episodes now. Done jobs, the air window, and watched state do not block.

    A file already on disk needs ``confirm``. A job still in progress is skipped.
    """
    del _principal
    if (body.item_id is None) == (body.season is None):
        raise Problem(422, "Requête invalide", "Indiquez un épisode ou une saison.")
    media = _get_media(db, media_id)
    if body.item_id is not None:
        item = db.get(MediaItem, body.item_id)
        if item is None or item.media_id != media.id:
            raise Problem(404, "Introuvable", "Épisode introuvable.")
        targets = [item]
    else:
        targets = list(
            db.scalars(
                select(MediaItem)
                .where(MediaItem.media_id == media.id, MediaItem.season == body.season)
                .order_by(MediaItem.episode, MediaItem.id)
            ).all()
        )
        if not targets:
            raise Problem(404, "Introuvable", "Cette saison n'a pas d'épisode.")
    from mediatracker.core.force_download import force_downloads
    from mediatracker.core.source_availability import (
        DOWNLOAD_BLOCKED_CODE,
        DOWNLOAD_BLOCKED_MESSAGE,
        any_download_source_available,
    )

    settings = request.app.state.settings
    if not any_download_source_available(db, settings):
        raise Problem(
            422,
            "Sources indisponibles",
            DOWNLOAD_BLOCKED_MESSAGE,
            extra={"code": DOWNLOAD_BLOCKED_CODE},
        )

    result = force_downloads(
        db,
        request.app.state.settings,
        media,
        targets,
        confirm=body.confirm,
        now=datetime.now(timezone.utc),
    )
    if body.item_id is not None and result.busy and not result.queued and not result.local:
        raise Problem(409, "Conflit", result.message)
    db.commit()
    return ForceDownloadOut(
        queued=len(result.queued),
        skipped_busy=len(result.busy),
        needs_confirm=result.needs_confirm,
        local=[
            ForceLocalOut(item_id=hit.item.id, season=hit.item.season, episode=hit.item.episode, path=hit.path)
            for hit in result.local
        ],
        message=result.message,
    )


@router.post("/{media_id}/reanalyze", response_model=ReanalyzeOut)
def reanalyze_fiche(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ReanalyzeOut:
    """Rescan canonical library folders for this fiche. Does not move or rename files."""
    del _principal
    from mediatracker.core.reanalyze import ReanalyzeBusy, reanalyze_media

    media = _get_media(db, media_id)
    try:
        report = reanalyze_media(db, request.app.state.settings, media, now=datetime.now(timezone.utc))
    except ReanalyzeBusy:
        raise Problem(409, "Conflit", "Une réanalyse est déjà en cours pour cette fiche.") from None
    db.commit()
    return ReanalyzeOut(found=report.found, new=report.new, absent=report.absent)


@router.post("/{media_id}/seasons/{number}/reanalyze", response_model=ReanalyzeOut)
def reanalyze_season(
    media_id: int,
    number: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ReanalyzeOut:
    """Rescan canonical folders for one season. Does not move or rename files."""
    del _principal
    from mediatracker.core.reanalyze import ReanalyzeBusy, reanalyze_media

    media = _get_media(db, media_id)
    if media.type != "series":
        raise Problem(422, "Requête invalide", "La réanalyse par saison ne s'applique pas à un film.")
    try:
        report = reanalyze_media(
            db,
            request.app.state.settings,
            media,
            season=number,
            now=datetime.now(timezone.utc),
        )
    except ReanalyzeBusy:
        raise Problem(409, "Conflit", "Une réanalyse est déjà en cours pour cette fiche.") from None
    db.commit()
    return ReanalyzeOut(found=report.found, new=report.new, absent=report.absent)


@router.get("/{media_id}", response_model=MediaOut)
def get_media(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> MediaOut:
    del _principal
    row = _get_media(db, media_id)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
    _sync_library_claims(db, request, row, items)
    file_status_index = FileStatusIndex.load(db, items)
    return _to_media(row, items, db, request.app.state.settings, file_status_index=file_status_index)


@router.patch("/{media_id}", response_model=MediaOut)
def update_media(
    media_id: int,
    body: MediaPatch,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> MediaOut:
    del _principal
    row = _get_media(db, media_id)
    previous_tmdb = row.tmdb_id
    previous_title = row.title
    previous_year = row.year
    previous_quality_profile = row.quality_profile_id
    previous_fallback_chain = row.fallback_profile_ids
    data = body.model_dump(exclude_unset=True)
    if "tmdb_id" in data and data["tmdb_id"] is not None and data["tmdb_id"] != previous_tmdb:
        if not isinstance(data["tmdb_id"], int) or data["tmdb_id"] < 1:
            raise Problem(422, "Requête invalide", "Identifiant TMDB invalide.")
        clash = db.scalar(
            select(Media).where(Media.tmdb_id == data["tmdb_id"], Media.type == row.type, Media.id != row.id)
        )
        if clash is not None:
            raise Problem(409, "Conflit", "Une autre fiche porte déjà cet identifiant TMDB.")
    if "series_status" in data:
        _check_status(row.type, data["series_status"])
    if "new_episode_policy" in data and data["new_episode_policy"] is not None:
        _check_policy(data["new_episode_policy"])
        row.auto_download = data["new_episode_policy"] == "auto"
    if "pref_quality" in data and data["pref_quality"] is not None and data["pref_quality"] not in QUALITIES:
        raise Problem(422, "Requête invalide", "Qualité inconnue.")
    if "pref_language" in data and data["pref_language"] is not None and data["pref_language"] not in LANGUAGES:
        raise Problem(422, "Requête invalide", "Langue inconnue.")
    if "source_order" in data:
        raw = data["source_order"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            data["source_order"] = None
        elif isinstance(raw, str):
            from mediatracker.core.selection import parse_download_source_order

            try:
                data["source_order"] = ",".join(parse_download_source_order(raw))
            except ValueError as exc:
                raise Problem(422, "Requête invalide", str(exc)) from exc
        else:
            raise Problem(422, "Requête invalide", "L'ordre des sources est invalide.")
    if "quality_profile_id" in data and data["quality_profile_id"] is not None:
        from mediatracker.db.models import QualityProfile

        if db.get(QualityProfile, data["quality_profile_id"]) is None:
            raise Problem(422, "Requête invalide", "Profil de qualité introuvable.")
    if "fallback_profile_ids" in data:
        from mediatracker.core.profile_fallback import (
            encode_media_fallback_profile_ids,
            validate_profile_fallback_ids,
        )

        ids = data.pop("fallback_profile_ids")
        if ids is None:
            row.fallback_profile_ids = None
        else:
            from mediatracker.settings_store import SettingsError

            try:
                validate_profile_fallback_ids(db, ids)
            except SettingsError as exc:
                raise Problem(422, "Requête invalide", str(exc)) from exc
            row.fallback_profile_ids = encode_media_fallback_profile_ids(list(ids))
    turning_auto = data.get("new_episode_policy") == "auto"
    pausing = data.get("on_hold") is True
    resuming = data.get("on_hold") is False
    patch_followed = "followed" in data
    following = data.get("followed") is True
    unfollowing = patch_followed and data.get("followed") is False
    moment = datetime.now(timezone.utc)
    tmdb_refresh: list[MediaItem] | None = None
    tmdb_fetched = False
    tmdb_warning: str | None = None
    if "tmdb_id" in data and data["tmdb_id"] != previous_tmdb and data["tmdb_id"] is not None:
        from mediatracker.core.tmdb.id_replace import (
            build_tmdb_id_change_preview,
            tmdb_id_change_needs_confirm,
        )

        confirm = data.get("tmdb_id_confirm")
        if confirm is not None and confirm not in ("keep_episodes", "rebuild_episodes"):
            raise Problem(422, "Requête invalide", "Confirmation de changement TMDB invalide.")
        if tmdb_token.read_tmdb_token(db, request.app.state.settings.secret_key):
            fetched, imdb_id = _fetch_tmdb_title(
                db, request.app.state.settings.secret_key, data["tmdb_id"], row.type
            )
            if tmdb_id_change_needs_confirm(row, data["tmdb_id"]) and confirm is None:
                preview = build_tmdb_id_change_preview(db, row, fetched, new_tmdb_id=data["tmdb_id"])
                raise Problem(
                    409,
                    "Confirmation requise",
                    "Remplacement d'identifiant TMDB : choisir de conserver ou reconstruire les épisodes.",
                    extra=preview.as_extra(),
                )
            if confirm == "rebuild_episodes":
                from mediatracker.core.tmdb.id_replace import prepare_rebuild_detach_files

                prepare_rebuild_detach_files(db, row, fetched)
            row.tmdb_id = data["tmdb_id"]
            tmdb_refresh = _apply_fetched_tmdb(db, row, fetched, imdb_id=imdb_id, moment=moment)
            if confirm == "rebuild_episodes":
                from mediatracker.core.tmdb.id_replace import delete_orphan_episode_rows

                delete_orphan_episode_rows(db, row, fetched)
                tmdb_refresh = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
            tmdb_fetched = True
        else:
            if confirm == "rebuild_episodes":
                raise Problem(
                    422,
                    "Requête invalide",
                    "Reconstruction des épisodes impossible sans jeton TMDB configuré.",
                )
            if tmdb_id_change_needs_confirm(row, data["tmdb_id"]) and confirm is None:
                raise Problem(
                    409,
                    "Confirmation requise",
                    "Remplacement d'identifiant TMDB : configurer le jeton TMDB pour prévisualiser les épisodes.",
                    extra={
                        "needs_confirm": True,
                        "tmdb_preview_available": False,
                        "previous_tmdb_id": previous_tmdb,
                        "new_tmdb_id": data["tmdb_id"],
                    },
                )
            tmdb_warning = "Identifiant enregistré ; métadonnées non récupérées (pas de jeton TMDB)."
    if "tmdb_id" in data and data["tmdb_id"] is not None and data["tmdb_id"] != previous_tmdb:
        row.tmdb_identify_status = None
        row.tmdb_identify_candidates = None
        if row.last_repair_summary == "À identifier TMDB.":
            row.last_repair_summary = None
    payload = {k: v for k, v in data.items() if k != "followed"} if patch_followed else data
    for key, value in payload.items():
        if key == "tmdb_id" and tmdb_fetched:
            continue
        setattr(row, key, value)
    if following:
        from mediatracker.core.follow import follow

        follow(db, None, media=row, source="patch", moment=moment)
    elif unfollowing:
        from mediatracker.core.follow import unfollow

        unfollow(db, row, reason="user", settings=request.app.state.settings, moment=moment, emit_event=False)
    if pausing:
        row.catch_up_pending = False
    elif turning_auto or (resuming and row.new_episode_policy == "auto"):
        row.catch_up_pending = True
        row.next_source_poll_at = moment
    chain_changed = (
        "fallback_profile_ids" in body.model_dump(exclude_unset=True)
        and row.fallback_profile_ids != previous_fallback_chain
    )
    if row.followed and (
        chain_changed
        or ("quality_profile_id" in data and data.get("quality_profile_id") != previous_quality_profile)
    ):
        from mediatracker.core.quality_profile import refresh_media_poll_schedule

        refresh_media_poll_schedule(row, moment)
    if "tmdb_id" in data and row.tmdb_id != previous_tmdb:
        from mediatracker.library.jellyfin_push import note_manual_tmdb

        note_manual_tmdb(
            db,
            row,
            moment,
            previous_tmdb=previous_tmdb,
            previous_title=previous_title,
            previous_year=previous_year,
        )
    row._tmdb_refresh_warning = tmdb_warning  # type: ignore[attr-defined]
    db.commit()
    db.refresh(row)
    if tmdb_warning:
        row._tmdb_refresh_warning = tmdb_warning  # type: ignore[attr-defined]
    items = tmdb_refresh if tmdb_refresh is not None else list(
        db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all()
    )
    file_status_index = FileStatusIndex.load(db, items)
    return _to_media(row, items, db, request.app.state.settings, file_status_index=file_status_index)


@router.get("/{media_id}/identity", response_model=list[IdentityChangeOut])
def list_identity(
    media_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> list[IdentityChangeOut]:
    """Journal of TMDB changes for this fiche, newest first."""
    del _principal
    from mediatracker.db.models import IdentityChange

    _get_media(db, media_id)
    rows = list(
        db.scalars(
            select(IdentityChange).where(IdentityChange.media_id == media_id).order_by(IdentityChange.id.desc())
        ).all()
    )
    return [IdentityChangeOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/{media_id}/identity/{change_id}/undo", response_model=IdentityChangeOut)
def undo_identity(
    media_id: int,
    change_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> IdentityChangeOut:
    """Reverse one journal row. The restored id is a MediaTracker write and is pushed."""
    del _principal
    from mediatracker.db.models import IdentityChange
    from mediatracker.library.identity_sync import IdentityError, undo

    row = _get_media(db, media_id)
    try:
        change = undo(db, row, change_id, datetime.now(timezone.utc))
    except IdentityError as exc:
        status = 404 if exc.code == "NOT_FOUND" else 409
        raise Problem(status, "Identifiant", exc.message) from exc
    db.commit()
    fresh = db.get(IdentityChange, change.id)
    return IdentityChangeOut.model_validate(fresh, from_attributes=True)


@router.post("/{media_id}/unfollow", response_model=MediaOut)
def unfollow_media(
    media_id: int,
    body: UnfollowIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> MediaOut:
    """Stop polling, notifications, and queued downloads. Files and watched history stay.

    ``trash`` moves the files to the bin. The fiche can be followed again later.
    """
    del _principal
    row = _get_media(db, media_id)
    moment = datetime.now(timezone.utc)
    from mediatracker.core.follow import unfollow

    unfollow(
        db,
        row,
        reason="user",
        settings=request.app.state.settings,
        moment=moment,
        trash=body.trash,
        cancel_queue=True,
        clear_schedule_flags=True,
    )
    db.commit()
    if body.trash:
        from mediatracker.core.cleanup import CleanupError, delete_files

        try:
            delete_files(db, request.app.state.settings, media_id=row.id, scope="media", season=None, item_id=None)
        except CleanupError as exc:
            raise Problem(409, "Conflit", str(exc)) from exc
    db.refresh(row)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
    file_status_index = FileStatusIndex.load(db, items)
    return _to_media(row, items, db, request.app.state.settings, file_status_index=file_status_index)


def _cancel_queue(
    db: Session,
    media_id: int,
    now: datetime,
    settings,
    *,
    message: str = "Suivi arrêté.",
    code: str = "UNFOLLOWED",
) -> None:
    from mediatracker.core.pipeline import _downloader, cancel_job

    item_ids = list(db.scalars(select(MediaItem.id).where(MediaItem.media_id == media_id)).all())
    if not item_ids:
        return
    rows = list(
        db.scalars(
            select(Download).where(
                Download.media_item_id.in_(item_ids),
                Download.state.not_in(("done", "cancelled")),
            )
        ).all()
    )
    _provider, downloader = _downloader(db, settings, None)
    del _provider
    for download in rows:
        try:
            cancel_job(db, download, downloader, now=now)
        except RuntimeError:
            continue
        download.error = message
        download.error_code = code


def _drop_history(db: Session, media_id: int) -> None:
    """Remove the fiche history. Rename-journal rows have no media id and stay."""
    from mediatracker.db.models import Event, WebhookDelivery

    item_ids = list(db.scalars(select(MediaItem.id).where(MediaItem.media_id == media_id)).all())
    filters = [Event.media_id == media_id]
    if item_ids:
        filters.append(Event.item_id.in_(item_ids))
    event_ids = list(db.scalars(select(Event.id).where(or_(*filters))).all())
    if not event_ids:
        return
    db.execute(sql_delete(WebhookDelivery).where(WebhookDelivery.event_id.in_(event_ids)))
    db.execute(sql_delete(Event).where(Event.id.in_(event_ids)))


def _delete_fiche(db: Session, settings, media_id: int, *, trash: bool) -> None:
    row = _get_media(db, media_id)
    moment = datetime.now(timezone.utc)
    if trash:
        from mediatracker.core.cleanup import CleanupError, delete_files

        try:
            delete_files(
                db,
                settings,
                media_id=row.id,
                scope="media",
                season=None,
                item_id=None,
                now=moment,
            )
        except CleanupError as exc:
            raise Problem(409, "Conflit", str(exc)) from exc
        row = _get_media(db, media_id)
    _cancel_queue(db, row.id, moment, settings, message="Fiche supprimée.", code="DELETED")
    _drop_history(db, row.id)
    db.delete(row)
    db.commit()


class BulkDeleteIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    trash: bool = False


class BulkDeleteOut(BaseModel):
    deleted: list[int]
    trash: bool


@router.delete("/bulk", response_model=BulkDeleteOut)
def delete_media_bulk(
    body: BulkDeleteIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> BulkDeleteOut:
    """Delete several fiches. ``trash`` moves files to the bin; the default keeps them."""
    del _principal
    ids = list(dict.fromkeys(body.ids))
    for media_id in ids:
        _get_media(db, media_id)
    for media_id in ids:
        _delete_fiche(db, request.app.state.settings, media_id, trash=body.trash)
    return BulkDeleteOut(deleted=ids, trash=body.trash)


@router.delete("/{media_id}", status_code=204)
def delete_media(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
    trash: bool = False,
) -> Response:
    """Delete the fiche. Files stay unless ``trash`` is true, and then they are never unlinked."""
    del _principal
    _delete_fiche(db, request.app.state.settings, media_id, trash=trash)
    return Response(status_code=204)


def _seasons_out(
    db: Session,
    media_id: int,
    rows: list[MediaItem],
    *,
    settings: Settings | None = None,
    media: Media | None = None,
) -> list[SeasonOut]:
    rows_sorted = sorted(rows, key=lambda item: (item.season is None, item.season or 0, item.episode or 0, item.id))
    item_ids = [row.id for row in rows_sorted if row.id is not None]
    busy, progress_ids = _download_activity_ids(db, item_ids)
    file_status_index = FileStatusIndex.load(db, rows_sorted)
    if media is None:
        media = db.get(Media, media_id)
    episode_profile_index = None
    if settings is not None and media is not None:
        from mediatracker.core.profile_replacement import EpisodeProfileIndex

        episode_profile_index = EpisodeProfileIndex.load(db, media, rows_sorted, settings)
    meta = {
        season.number: season
        for season in db.scalars(select(MediaSeason).where(MediaSeason.media_id == media_id)).all()
    }
    buckets: dict[int | None, list[tuple[MediaItem, EpisodeOut]]] = {}
    for row in rows_sorted:
        buckets.setdefault(row.season, []).append(
            (
                row,
                _episode_out(
                    row,
                    download_active=row.id in busy,
                    download_busy=row.id in progress_ids,
                    file_status_index=file_status_index,
                    episode_profile_index=episode_profile_index,
                ),
            )
        )
    seasons: list[SeasonOut] = []
    for number, pairs in buckets.items():
        season = meta.get(number) if number is not None else None
        name = season.name if season is not None else None
        if number == 0 and not name:
            name = "Spéciaux"
        episode_rows = [item for item, _out in pairs]
        counts = file_status_index.counts_for(episode_rows)
        seasons.append(
            SeasonOut(
                number=number,
                name=name,
                air_date=season.air_date if season is not None else None,
                episode_count=season.episode_count if season is not None else len(pairs),
                keep=season.keep if season is not None else False,
                file_status=aggregate_file_status(counts),
                file_status_counts=_counts_out(counts),
                episodes=[out for _item, out in pairs],
            )
        )
    return seasons


def fiche_sse_payload(db: Session, settings, media_id: int) -> dict[str, Any]:
    """Read-only fiche state for the SSE hub (no library refresh or commit)."""
    media = _get_media(db, media_id)
    rows = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media_id)).all())
    file_status_index = FileStatusIndex.load(db, rows)
    media_out = _to_media(media, rows, db, settings, file_status_index=file_status_index)
    seasons = _seasons_out(db, media_id, rows, settings=settings, media=media)
    return {
        "media": media_out.model_dump(mode="json"),
        "seasons": [season.model_dump(mode="json") for season in seasons],
    }


@router.get("/{media_id}/items", response_model=EpisodeListOut)
def list_items(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> EpisodeListOut:
    del _principal
    media = _get_media(db, media_id)
    rows = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media_id)).all())
    _sync_library_claims(db, request, media, rows)
    return EpisodeListOut(
        seasons=_seasons_out(db, media_id, rows, settings=request.app.state.settings, media=media)
    )


@router.patch("/{media_id}/seasons/{number}", response_model=SeasonOut)
def update_season(
    media_id: int,
    number: int,
    body: SeasonPatch,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> SeasonOut:
    del _principal
    media = _get_media(db, media_id)
    if media.type != "series":
        raise Problem(422, "Requête invalide", "Un film n'a pas de saison.")
    if number < 0:
        raise Problem(422, "Requête invalide", "Numéro de saison invalide.")
    row = db.scalar(select(MediaSeason).where(MediaSeason.media_id == media_id, MediaSeason.number == number))
    if row is None:
        row = MediaSeason(media_id=media_id, number=number, keep=body.keep)
        db.add(row)
    else:
        row.keep = body.keep
    db.commit()
    db.refresh(row)
    episodes = list(
        db.scalars(select(MediaItem).where(MediaItem.media_id == media_id, MediaItem.season == number)).all()
    )
    busy = _busy_item_ids(db, [item.id for item in episodes])
    progress_ids = _in_progress_ids(db, [item.id for item in episodes])
    file_status_index = FileStatusIndex.load(db, episodes)
    counts = file_status_index.counts_for(episodes)
    return SeasonOut(
        number=row.number,
        name=row.name or ("Spéciaux" if number == 0 else None),
        air_date=row.air_date,
        episode_count=row.episode_count if row.episode_count is not None else len(episodes),
        keep=row.keep,
        file_status=aggregate_file_status(counts),
        file_status_counts=_counts_out(counts),
        episodes=[
            _episode_out(
                item,
                download_active=item.id in busy,
                download_busy=item.id in progress_ids,
                file_status_index=file_status_index,
            )
            for item in episodes
        ],
    )


@router.post("/{media_id}/items", response_model=EpisodeOut, status_code=201)
def create_item(
    media_id: int,
    body: EpisodeIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> EpisodeOut:
    del _principal
    _get_media(db, media_id)
    row = MediaItem(
        media_id=media_id,
        season=body.season,
        episode=body.episode,
        title=body.title,
        air_date=body.air_date,
        source_available=body.source_available,
        keep=body.keep,
        state="available" if body.source_available else "missing",
    )
    _apply_flags(row, downloaded=body.downloaded, watched=body.watched)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Problem(409, "Conflit", "Cet épisode existe déjà.") from exc
    db.refresh(row)
    file_status_index = FileStatusIndex.load(db, [row])
    return _episode_out(row, file_status_index=file_status_index)


class RedownloadOut(BaseModel):
    status: str
    message: str
    download_id: int | None = None


class ProfileReplacementActionOut(BaseModel):
    ok: bool
    message: str
    download_id: int | None = None


@item_router.post("/{item_id}/profile-replacement/replace", response_model=ProfileReplacementActionOut)
def replace_profile_version(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ProfileReplacementActionOut:
    del _principal
    row = db.get(MediaItem, item_id)
    if row is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    media = db.get(Media, row.media_id)
    if media is None:
        raise Problem(404, "Introuvable", "Fiche introuvable.")
    from mediatracker.core.profile_replacement import start_replace

    result = start_replace(db, request.app.state.settings, media, row, now=datetime.now(timezone.utc))
    if not result.ok:
        raise Problem(422, "Requête invalide", result.message)
    db.commit()
    return ProfileReplacementActionOut(ok=True, message=result.message, download_id=result.download_id)


@item_router.post("/{item_id}/profile-replacement/dismiss", response_model=ProfileReplacementActionOut)
def dismiss_profile_replacement(
    item_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ProfileReplacementActionOut:
    del _principal
    row = db.get(MediaItem, item_id)
    if row is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    from mediatracker.core.profile_replacement import dismiss_proposal

    if not dismiss_proposal(db, row, now=datetime.now(timezone.utc)):
        raise Problem(404, "Introuvable", "Aucune proposition active.")
    db.commit()
    return ProfileReplacementActionOut(ok=True, message="Proposition ignorée.")


@item_router.post("/{item_id}/redownload", response_model=RedownloadOut)
def redownload_episode(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RedownloadOut:
    """Re-queue a download from stored links when the file is absent from disk."""
    del _principal
    row = db.get(MediaItem, item_id)
    if row is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    media = db.get(Media, row.media_id)
    if media is None:
        raise Problem(404, "Introuvable", "Fiche introuvable.")
    from mediatracker.core.redownload import redownload_item

    result = redownload_item(db, request.app.state.settings, media, row, now=datetime.now(timezone.utc))
    if result.status == "busy":
        raise Problem(409, "Conflit", result.message)
    if result.status == "not_absent":
        raise Problem(422, "Requête invalide", result.message)
    db.commit()
    return RedownloadOut(
        status=result.status,
        message=result.message,
        download_id=result.download.id if result.download is not None else None,
    )


@item_router.patch("/{item_id}", response_model=EpisodeOut)
def update_item(
    item_id: int,
    body: EpisodePatch,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> EpisodeOut:
    del _principal
    row = db.get(MediaItem, item_id)
    if row is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    data = body.model_dump(exclude_unset=True)
    downloaded = data.pop("downloaded", None)
    watched = data.pop("watched", None)
    for key, value in data.items():
        setattr(row, key, value)
    if row.season is not None and row.episode is not None and row.state == "ambiguous":
        row.state = "available"
    if "source_available" in data and data["source_available"] and row.state == "missing":
        row.state = "available"
    _apply_flags(row, downloaded=downloaded, watched=watched)
    db.commit()
    db.refresh(row)
    file_status_index = FileStatusIndex.load(db, [row])
    return _episode_out(row, file_status_index=file_status_index)


class WatchIn(BaseModel):
    item_id: int | None = Field(default=None, ge=1)
    season: int | None = Field(default=None, ge=0, le=40)
    until_season: int | None = Field(default=None, ge=0, le=40)
    until_episode: int | None = Field(default=None, ge=0, le=999)
    all: bool = False
    undo: bool = False


class WatchOut(BaseModel):
    updated: int
    attached_count: int = 0
    message: str


@router.post("/unfollow-identified", response_model=WatchOut)
def unfollow_identified(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> WatchOut:
    """Stop following fiches created by identification. Files stay on disk."""
    del _principal
    rows = list(
        db.scalars(select(Media).where(Media.follow_origin == "identification", Media.followed.is_(True))).all()
    )
    from mediatracker.core.follow import unfollow

    for row in rows:
        unfollow(db, row, reason="identified")
    db.commit()
    count = len(rows)
    return WatchOut(updated=count, message=f"{count} fiche(s) ne sont plus suivies. Les fichiers restent.")


@router.post("/{media_id}/watch", response_model=WatchOut)
def watch_media(
    media_id: int,
    body: WatchIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> WatchOut:
    del _principal
    from mediatracker.core.manual_watch import mark_manual

    row = _get_media(db, media_id)
    updated, changed = mark_manual(
        db,
        row,
        item_id=body.item_id,
        season=body.season,
        until_season=body.until_season,
        until_episode=body.until_episode,
        all_entire=body.all,
        undo=body.undo,
    )
    _push_watched(request, db, changed, body.undo)
    db.commit()
    label = "film" if row.type == "film" else "épisode"
    if body.undo:
        message = f"{updated} {label}(s) marqués non vus."
    elif row.type == "film":
        message = f"{updated} film(s) marqué(s) vu(s)."
    else:
        message = f"{updated} épisode(s) marqués vus. Ils ne seront pas téléchargés."
    return WatchOut(updated=updated, message=message)


class LibraryFolderChoice(BaseModel):
    path: str
    name: str
    year: int | None
    tmdb_id: int | None
    score: int
    file_count: int


class LibraryFolderPage(BaseModel):
    items: list[LibraryFolderChoice]


class LibraryFolderIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class LibraryFolderBound(BaseModel):
    path: str
    attached: int
    title: str


class JellyfinEpisodeDiag(BaseModel):
    item_id: int
    season: int | None
    episode: int | None
    matched: bool
    played: bool
    reason: str


class JellyfinDiagOut(BaseModel):
    configured: bool
    series_id: str | None = None
    series_ids: list[str] = []
    series_name: str | None = None
    manual_series_id: str | None = None
    synced_at: str | None = None
    detail: str
    summary: str | None = None
    episodes: list[JellyfinEpisodeDiag] = []
    unmatched: list[JellyfinEpisodeDiag] = []


class JellyfinSeriesHit(BaseModel):
    id: str
    name: str
    year: int | None = None
    original_title: str | None = None


class JellyfinSeriesPage(BaseModel):
    items: list[JellyfinSeriesHit]


class JellyfinLinkIn(BaseModel):
    series_id: str = Field(min_length=1, max_length=64)


class JellyfinLinkOut(BaseModel):
    series_id: str | None


@router.get("/{media_id}/library-folders", response_model=LibraryFolderPage)
def media_library_folders(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> LibraryFolderPage:
    """Library folders of the same type, closest title first."""
    del _principal
    from mediatracker.core.library_plan import list_folders
    from mediatracker.core.storage import library_skip, storage_layout
    from mediatracker.library.scanner import folder_rank, library_roots

    media = _get_media(db, media_id)
    movies, series = library_roots(db, request.app.state.settings)
    skip = library_skip(storage_layout(db, request.app.state.settings))
    rows = []
    for folder in list_folders(movies, series, skip=skip):
        if folder.media_type != media.type:
            continue
        rows.append(
            LibraryFolderChoice(
                path=folder.path,
                name=folder.name,
                year=folder.year,
                tmdb_id=folder.tmdb_id,
                score=folder_rank(folder, media),
                file_count=len(folder.files),
            )
        )
    rows.sort(key=lambda item: (-item.score, item.name))
    return LibraryFolderPage(items=rows)


@router.post("/{media_id}/library-folder", response_model=LibraryFolderBound)
def bind_media_folder(
    media_id: int,
    body: LibraryFolderIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> LibraryFolderBound:
    """Point a library folder at this fiche and attach the files. No download is queued."""
    del _principal
    from mediatracker.api.v1.library import _locate
    from mediatracker.core.library_plan import LibraryPlanError
    from mediatracker.library.scanner import bind_library_folder

    media = _get_media(db, media_id)
    if media.tmdb_id is None:
        raise Problem(422, "Bibliothèque", "Cette fiche n'a pas d'identifiant TMDB.")
    settings = request.app.state.settings
    folder, _root, kind = _locate(db, settings, body.path)
    if kind != media.type:
        raise Problem(422, "Bibliothèque", "Le dossier n'est pas du même type que la fiche.")
    try:
        renamed, bound, attached = bind_library_folder(
            db,
            settings,
            folder,
            tmdb_id=media.tmdb_id,
            media_type=media.type,
            media=media,
        )
    except LibraryPlanError as exc:
        raise Problem(422, "Bibliothèque", exc.message) from exc
    db.commit()
    return LibraryFolderBound(path=str(renamed), attached=attached, title=bound.title)


class DeadLinkOut(BaseModel):
    id: int
    url: str
    hoster: str | None
    season: int | None
    episode: int | None
    title: str | None
    last_error: str | None


@router.get("/{media_id}/dead-links", response_model=list[DeadLinkOut])
def list_dead_links(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> list[DeadLinkOut]:
    """Known dead download links for this fiche (manual or source pipeline)."""
    del _principal
    from mediatracker.core.catalog_source_visibility import link_from_visible_source

    settings = request.app.state.settings
    _get_media(db, media_id)
    rows = db.execute(
        select(Link, MediaItem)
        .join(MediaItem, Link.media_item_id == MediaItem.id)
        .where(MediaItem.media_id == media_id, Link.dead.is_(True))
        .order_by(MediaItem.season.asc().nulls_first(), MediaItem.episode.asc().nulls_first(), Link.id)
    ).all()
    return [
        DeadLinkOut(
            id=link.id,
            url=link.url,
            hoster=link.hoster,
            season=item.season,
            episode=item.episode,
            title=item.title,
            last_error=link.last_error,
        )
        for link, item in rows
        if link_from_visible_source(db, settings, link)
    ]


class RepairStepOut(BaseModel):
    name: str
    ok: bool
    error: str | None = None
    detail: str = ""


class RepairOut(BaseModel):
    at: datetime
    summary: str
    detail: str
    steps: list[RepairStepOut]
    files_found: int
    files_missing: int
    links_available: int
    corrected: list[str] = Field(default_factory=list)
    file_status: str
    file_status_counts: FileStatusCountsOut
    detached: int = 0
    removed: int = 0
    restored: int = 0
    attached: int = 0


@router.post("/{media_id}/repair", response_model=RepairOut)
def repair_fiche(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RepairOut:
    """Repair a fiche in one click: disk check, reanalyze, source, Jellyfin, status. No moves or trash."""
    del _principal
    from mediatracker.core.fiche_repair import FicheRepairBusy
    from mediatracker.core.fiche_repair import repair_fiche as run_repair

    media = _get_media(db, media_id)
    try:
        report = run_repair(db, request.app.state.settings, media, now=datetime.now(timezone.utc))
    except FicheRepairBusy:
        raise Problem(409, "Conflit", "Une réparation est déjà en cours pour cette fiche.") from None
    from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker

    wake_worker(db, request.app.state.settings, REASON_API_MEDIA, commit=False)
    db.commit()
    return RepairOut(
        at=report.at,
        summary=report.summary,
        detail=report.detail,
        steps=[
            RepairStepOut(name=step.name, ok=step.ok, error=step.error, detail=step.detail) for step in report.steps
        ],
        files_found=report.files_found,
        files_missing=report.files_missing,
        links_available=report.links_available,
        corrected=report.corrected,
        file_status=report.file_status,
        file_status_counts=_counts_out(report.file_status_counts),
        detached=report.detached,
        removed=report.removed,
        restored=report.restored,
        attached=report.attached,
    )


@router.get("/{media_id}/jellyfin", response_model=JellyfinDiagOut)
def jellyfin_diagnostic(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> JellyfinDiagOut:
    del _principal
    media = _get_media(db, media_id)
    return _jellyfin_diag(request, db, media, apply=False)


@router.post("/{media_id}/jellyfin/sync", response_model=JellyfinDiagOut)
def jellyfin_resync(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> JellyfinDiagOut:
    del _principal
    media = _get_media(db, media_id)
    return _jellyfin_diag(request, db, media, apply=True)


def _jellyfin_diag(request: Request, db: Session, media: Media, *, apply: bool) -> JellyfinDiagOut:
    from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, from_api_item
    from mediatracker.integrations.jellyfin_sync import (
        LAST_POLL_AT,
        apply_watches,
        diagnose_media,
        jellyfin_settings,
    )

    settings = request.app.state.settings
    url, api_key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    if not url or not api_key:
        return JellyfinDiagOut(configured=False, detail="Jellyfin n'est pas configuré.")
    # The Jellyfin call is slow. Release the session so it does not sit on the writer lock.
    from mediatracker.db.session import commit_retry

    commit_retry(db)
    client = JellyfinClient(url, api_key, user_id or None)
    try:
        from mediatracker.integrations.jellyfin_sync import resolve_configured_user

        resolved = resolve_configured_user(db, client, user_id)
        raw_items = client.user_items(resolved)
        series_index = client.series_index(resolved)
    except JellyfinError as exc:
        status = 422 if exc.code in {"USER_NOT_FOUND", "NO_USER"} else 502
        raise Problem(status, "Jellyfin", exc.message) from exc
    finally:
        client.close()
    watches = [item for item in (from_api_item(row, series_index) for row in raw_items) if item is not None]
    if apply:
        moment = datetime.now(timezone.utc)
        apply_watches(
            db,
            watches,
            prefix or None,
            moment,
            user_filter=resolved,
            path_map=path_map,
            series_index=series_index,
        )
        from mediatracker.integrations.jellyfin_sync import _touch

        _touch(db, LAST_POLL_AT, moment)
        touch_fiche_checked(media, moment)
        db.commit()
    synced = db.get(Setting, LAST_POLL_AT)
    report = diagnose_media(db, media, watches, synced.value if synced is not None else None, series_index)
    return _diag_out(report)


def _diag_out(report: dict) -> JellyfinDiagOut:
    if report.get("summary") and not report.get("series_ids"):
        detail = str(report["summary"])
    else:
        linked = report.get("series_name") or "série non liée"
        detail = f"Jellyfin : {linked}."
        count = len(report.get("series_ids") or [])
        if count > 1:
            detail = f"{detail} {count} séries retenues."
    return JellyfinDiagOut(
        configured=True,
        series_id=report.get("series_id"),
        series_ids=list(report.get("series_ids") or []),
        series_name=report.get("series_name"),
        manual_series_id=report.get("manual_series_id"),
        synced_at=report.get("synced_at"),
        detail=detail,
        summary=report.get("summary"),
        episodes=[JellyfinEpisodeDiag(**row) for row in report.get("episodes") or []],
        unmatched=[JellyfinEpisodeDiag(**row) for row in report.get("unmatched") or []],
    )


@router.get("/{media_id}/jellyfin/series", response_model=JellyfinSeriesPage)
def search_jellyfin_series(
    media_id: int,
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> JellyfinSeriesPage:
    """Search Jellyfin series by name so a fiche can be linked by hand."""
    del _principal
    _get_media(db, media_id)
    from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError
    from mediatracker.integrations.jellyfin_sync import jellyfin_settings, resolve_configured_user

    url, api_key, user_id, _prefix, _map = jellyfin_settings(db, request.app.state.settings)
    if not url or not api_key:
        raise Problem(422, "Jellyfin", "Jellyfin n'est pas configuré.")
    client = JellyfinClient(url, api_key, user_id or None)
    try:
        resolved = resolve_configured_user(db, client, user_id)
        rows = client.search_series(resolved, q.strip())
    except JellyfinError as exc:
        status = 422 if exc.code in {"USER_NOT_FOUND", "NO_USER"} else 502
        raise Problem(status, "Jellyfin", exc.message) from exc
    finally:
        client.close()
    items = []
    for row in rows:
        series_id = row.get("Id")
        name = row.get("Name")
        if not isinstance(series_id, str) or not series_id or not isinstance(name, str) or not name:
            continue
        year = row.get("ProductionYear") if isinstance(row.get("ProductionYear"), int) else None
        original = row.get("OriginalTitle") if isinstance(row.get("OriginalTitle"), str) else None
        items.append(JellyfinSeriesHit(id=series_id, name=name, year=year, original_title=original))
    return JellyfinSeriesPage(items=items)


@router.put("/{media_id}/jellyfin/link", response_model=JellyfinLinkOut)
def link_jellyfin_series(
    media_id: int,
    body: JellyfinLinkIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> JellyfinLinkOut:
    """Store a Jellyfin series id. Matching uses it before the automatic rules."""
    del _principal
    media = _get_media(db, media_id)
    media.jellyfin_series_id = body.series_id.strip()
    db.commit()
    return JellyfinLinkOut(series_id=media.jellyfin_series_id)


@router.delete("/{media_id}/jellyfin/link", response_model=JellyfinLinkOut)
def unlink_jellyfin_series(
    media_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> JellyfinLinkOut:
    del _principal
    media = _get_media(db, media_id)
    media.jellyfin_series_id = None
    db.commit()
    return JellyfinLinkOut(series_id=None)


def _push_watched(request: Request, db: Session, changed: list, undo: bool) -> None:
    import logging

    logger = logging.getLogger(__name__)
    row = db.get(Setting, "jellyfin_push_watched")
    if row is None or row.value != "true":
        return
    from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError
    from mediatracker.integrations.jellyfin_sync import jellyfin_settings

    url, key, user_id, _prefix, _mapping = jellyfin_settings(db, request.app.state.settings)
    if not url or not key:
        return
    client = JellyfinClient(url, key, user_id or None)
    try:
        from mediatracker.integrations.jellyfin_sync import resolve_configured_user

        try:
            user_id = resolve_configured_user(db, client, user_id)
        except JellyfinError:
            return
        for item in changed:
            jellyfin_id = item.jellyfin_item_id
            if not jellyfin_id:
                continue
            try:
                if undo:
                    client.mark_unplayed(user_id, jellyfin_id)
                elif item.watched:
                    client.mark_played(user_id, jellyfin_id)
            except JellyfinError:
                continue
            except Exception:
                logger.warning(
                    "Unexpected Jellyfin push failure for media_item=%s",
                    item.id,
                    exc_info=True,
                )
    except Exception:
        logger.warning("Jellyfin push watched aborted", exc_info=True)
    finally:
        client.close()
