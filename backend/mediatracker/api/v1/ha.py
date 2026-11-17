"""Compact Home Assistant summary and the actions behind the dashboard buttons.

``GET /ha/summary`` reads the database and the local disk only. It does not
call AllDebrid, TMDB, Jellyfin, aria2, or Wawacity. The buttons reuse the
existing catalog refresh and library scan, or ask the worker to poll sources
and to hold the download queue.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.catalog import refresh_catalog
from mediatracker.core.digital_releases import list_digital_releases
from mediatracker.core.disk import free_bytes, threshold_bytes
from mediatracker.core.pipeline import queue_pause
from mediatracker.core.queue_hold import downloads_held, set_downloads_held
from mediatracker.core.release_title import release_caption
from mediatracker.core.source_force import request_source_check, source_configured
from mediatracker.core.source_poll import close_source
from mediatracker.core.storage import device_id, incomplete_dir, storage_layout
from mediatracker.db.models import CatalogEntry, Download, Media, MediaItem, Provider
from mediatracker.integrations.tmdb import TMDB_IMAGE_BASE
from mediatracker.library.scanner import LAST_SCAN_KEY, force_scan
from mediatracker.providers.sources.loading import sources_for_catalog

router = APIRouter(prefix="/ha", tags=["homeassistant"])

_ACTIVE = ("resolving", "queued", "downloading", "post_processing")
_QUEUE = ("pending",)
_UPCOMING_LIMIT = 8
_WATCH_LIMIT = 8
_QUEUE_LIMIT = 5
_LATEST = 5
_HEALTH_RANK = {"down": 2, "degraded": 1}
_HEALTH_LABEL = {"ok": "ok", "degraded": "dégradé", "down": "indisponible"}
_GIB = 1024 * 1024 * 1024


class DownloadSummary(BaseModel):
    active: int
    progress: int
    speed: int
    paused: bool
    user_paused: bool
    pause_reason: str | None


class QueueItem(BaseModel):
    id: int
    state: str
    title: str
    progress: int


class QueueSummary(BaseModel):
    count: int
    items: list[QueueItem]


class WatchItem(BaseModel):
    media_title: str
    media_type: str
    label: str
    title: str | None


class WatchSummary(BaseModel):
    episodes: int
    films: int
    items: list[WatchItem]


class UpcomingItem(BaseModel):
    media_title: str
    label: str
    title: str | None
    air_date: date


class CatalogLatest(BaseModel):
    title: str
    year: int | None
    media_type: str
    poster_url: str | None
    version: str | None = None


class CatalogSummary(BaseModel):
    films: int
    series: int
    latest: list[CatalogLatest]


class SourceScanSummary(BaseModel):
    at: datetime | None
    media_title: str | None
    result: str | None
    message: str | None


class HealthSummary(BaseModel):
    status: str
    label: str
    component: str | None
    message: str | None


class DiskSummary(BaseModel):
    free_bytes: int | None
    free_gb: float | None


class UpdateSummary(BaseModel):
    available: bool
    current_sha: str | None = None
    remote_sha: str | None = None
    build_date: str | None = None
    detail: str | None = None
    checked_at: datetime | None = None


class DigitalReleaseOut(BaseModel):
    title: str
    poster_url: str | None
    overview: str | None = None
    date: date
    type: Literal["movie", "tv"]
    tmdb_id: int
    tracked: bool
    media_id: int | None = None


class DigitalReleasePage(BaseModel):
    items: list[DigitalReleaseOut]


class BuildSummary(BaseModel):
    version: str
    sha: str | None = None
    build_date: str | None = None


class HaSummary(BaseModel):
    downloads: DownloadSummary
    queue: QueueSummary
    to_watch: WatchSummary
    upcoming: list[UpcomingItem]
    catalog: CatalogSummary
    source_scan: SourceScanSummary
    health: HealthSummary
    disk: DiskSummary
    library_scan_at: datetime | None
    update_available: bool = False
    update: UpdateSummary | None = None
    digital_releases: list[DigitalReleaseOut]
    airing_series: list[DigitalReleaseOut] = Field(default_factory=list)
    build: BuildSummary


class ActionOut(BaseModel):
    ok: bool
    detail: str
    code: str | None = None


class UpdateOut(BaseModel):
    ok: bool
    update_available: bool
    current_sha: str | None = None
    remote_sha: str | None = None
    build_date: str | None = None
    detail: str
    checked_at: datetime | None = None
    version: str | None = None
    watchtower_configured: bool = False
    active_downloads: int = 0


class FollowIn(BaseModel):
    tmdb_id: int = Field(ge=1)
    type: Literal["movie", "tv"]
    auto_download: bool | None = None


class FollowOut(BaseModel):
    ok: bool = True
    already: bool
    title: str
    tmdb_id: int
    type: str
    attached: int = 0
    detail: str


@router.get("/summary", response_model=HaSummary)
def ha_summary(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> HaSummary:
    del _principal
    settings = request.app.state.settings
    update = _update(db)
    return HaSummary(
        downloads=_downloads(db),
        queue=_queue(db),
        to_watch=_to_watch(db),
        upcoming=_upcoming(db),
        catalog=_catalog(db, settings),
        source_scan=_source_scan(db),
        health=_health(db, settings),
        disk=_disk(db, settings),
        library_scan_at=_trim(_library_scan_at(db)),
        update_available=update.available,
        update=update,
        digital_releases=_releases(db, "movie"),
        airing_series=_releases(db, "tv"),
        build=_build(),
    )


@router.get("/releases", response_model=DigitalReleasePage)
def ha_releases(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> DigitalReleasePage:
    """Sorties numériques already cached. This route does not call TMDB."""
    del _principal
    return DigitalReleasePage(items=_releases(db, None))


@router.post("/actions/check-sources", response_model=ActionOut)
def ha_check_sources(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    del _principal
    settings = request.app.state.settings
    if not source_configured(db, settings):
        raise Problem(422, "Source", "Configurez l'URL Wawacity pour vérifier les sources.")
    scheduled = request_source_check(db)
    return ActionOut(ok=True, detail=f"{scheduled} fiche(s) seront vérifiées.")


@router.post("/actions/refresh-catalog", response_model=ActionOut)
def ha_refresh_catalog(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    del _principal
    settings = request.app.state.settings
    sources = sources_for_catalog(db, settings)
    if not sources:
        raise Problem(422, "Source", "Configurez l'URL Wawacity pour actualiser les nouveautés.")
    try:
        report = refresh_catalog(db, settings, sources=sources, force=True)
    finally:
        for _provider_id, _source_type, source in sources:
            close_source(source)
    if report.error:
        return ActionOut(ok=False, detail=report.error)
    return ActionOut(
        ok=True,
        detail=f"{report.added} ajout(s), {report.updated} mise(s) à jour.",
    )


@router.post("/actions/scan-library", response_model=ActionOut)
def ha_scan_library(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    del _principal
    report = force_scan(db, request.app.state.settings)
    return ActionOut(ok=True, detail=f"{report.seen} fichier(s) vu(s), {report.matched} reconnu(s).")


@router.post("/actions/downloads/pause", response_model=ActionOut)
def ha_pause_downloads(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    del _principal
    set_downloads_held(db, True, downloader=_downloader(db, request))
    return ActionOut(ok=True, detail="Téléchargements en pause.")


@router.post("/actions/downloads/resume", response_model=ActionOut)
def ha_resume_downloads(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    del _principal
    set_downloads_held(db, False, downloader=_downloader(db, request))
    return ActionOut(ok=True, detail="Téléchargements repris.")


@router.post("/actions/restart", response_model=ActionOut)
def ha_restart(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    """Pause aria2, commit, then exit 1 so Docker restarts the container."""
    del _principal
    from mediatracker.core.restart import pause_downloader, schedule_container_stop

    pause_downloader(_downloader(db, request))
    db.commit()
    schedule_container_stop(1)
    return ActionOut(ok=True, detail="Redémarrage en cours. Les téléchargements sont en pause.")


@router.get("/update", response_model=UpdateOut)
def ha_update_status(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> UpdateOut:
    del _principal
    from mediatracker.core.build_info import app_version
    from mediatracker.core.watchtower import watchtower_configured

    summary = _update(db)
    active = db.scalar(select(func.count()).select_from(Download).where(Download.state.in_(_ACTIVE))) or 0
    base = _update_out(summary)
    return base.model_copy(
        update={
            "checked_at": summary.checked_at,
            "version": app_version(),
            "watchtower_configured": watchtower_configured(db, request.app.state.settings),
            "active_downloads": int(active),
        }
    )


@router.post("/actions/check-update", response_model=UpdateOut)
def ha_check_update(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> UpdateOut:
    """Compare this image with GHCR, or with main if only a GitHub token is set."""
    del _principal
    from mediatracker.core.updates import UpdateError, check_for_update

    try:
        status = check_for_update(db, request.app.state.settings)
    except UpdateError as exc:
        raise Problem(422, "Mise à jour", exc.message) from exc
    db.commit()
    return _update_out(status)


@router.post("/actions/update-now", response_model=ActionOut)
def ha_update_now(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ActionOut:
    del _principal
    from fastapi.responses import JSONResponse

    from mediatracker.core.watchtower import request_update_now, watchtower_configured

    settings = request.app.state.settings
    if not watchtower_configured(db, settings):
        raise Problem(
            409,
            "Watchtower",
            "Indisponible : renseignez l'URL de Watchtower et son jeton dans Jetons d'API.",
        )
    result = request_update_now(db, settings)
    db.commit()
    body = ActionOut(ok=result.ok, detail=result.detail, code=result.code)
    if result.status == 202:
        return JSONResponse(body.model_dump(), status_code=202)
    if result.status == 502:
        raise Problem(502, "Watchtower", result.detail, extra={"code": result.code})
    return body


@router.post("/actions/follow", response_model=FollowOut)
def ha_follow(
    body: FollowIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> FollowOut:
    """Follow a TMDB id the same way the web form does. Already followed stays as-is."""
    del _principal
    from mediatracker.api.v1.media import MediaCreate, insert_followed
    from mediatracker.library.scanner import attach_known_files

    media_type = "film" if body.type == "movie" else "series"
    existing = db.scalar(select(Media).where(Media.tmdb_id == body.tmdb_id, Media.type == media_type))
    if existing is not None and existing.followed:
        return FollowOut(
            already=True,
            title=existing.title,
            tmdb_id=body.tmdb_id,
            type=body.type,
            detail=f"{existing.title} est déjà suivi.",
        )
    policy = None
    if body.auto_download is True:
        policy = "auto"
    elif body.auto_download is False:
        policy = "propose"
    row, _episodes = insert_followed(
        db,
        request.app.state.settings.secret_key,
        MediaCreate(type=media_type, tmdb_id=body.tmdb_id, new_episode_policy=policy),
    )
    attached = attach_known_files(db, request.app.state.settings, row)
    if attached:
        db.commit()
    detail = _follow_detail(row.title, media_type, attached)
    return FollowOut(
        already=False,
        title=row.title,
        tmdb_id=body.tmdb_id,
        type=body.type,
        attached=attached,
        detail=detail,
    )


def _follow_detail(title: str, media_type: str, attached: int) -> str:
    if not attached:
        return f"{title} est suivi."
    if media_type == "film":
        noun = "fichier déjà présent rattaché" if attached == 1 else "fichiers déjà présents rattachés"
    else:
        noun = "épisode déjà présent rattaché" if attached == 1 else "épisodes déjà présents rattachés"
    return f"{title} est suivi. {attached} {noun}."


def _build() -> BuildSummary:
    from mediatracker.core.build_info import app_version, build_date, short_sha

    return BuildSummary(version=app_version(), sha=short_sha(), build_date=build_date())


def _update(db: Session) -> UpdateSummary:
    from mediatracker.core.updates import stored_update

    status = stored_update(db)
    return UpdateSummary(
        available=status.available,
        current_sha=status.current_sha,
        remote_sha=status.remote_sha,
        build_date=status.build_date,
        detail=status.detail or None,
        checked_at=_trim(status.checked_at),
    )


def _update_out(status) -> UpdateOut:
    summary = status if isinstance(status, UpdateSummary) else None
    if summary is None:
        summary = UpdateSummary(
            available=status.available,
            current_sha=status.current_sha,
            remote_sha=status.remote_sha,
            build_date=status.build_date,
            detail=status.detail or None,
            checked_at=status.checked_at,
        )
    return UpdateOut(
        ok=True,
        update_available=summary.available,
        current_sha=summary.current_sha,
        remote_sha=summary.remote_sha,
        build_date=summary.build_date,
        detail=summary.detail or "Pas encore de vérification.",
    )


def _downloads(db: Session) -> DownloadSummary:
    rows = db.scalars(select(Download).where(Download.state.in_(_ACTIVE))).all()
    progress = _global_progress(rows)
    speed = sum(row.speed or 0 for row in rows)
    paused, code, _message = queue_pause(db)
    reason = _pause_reason(code) if paused else None
    if not paused and downloads_held(db):
        paused = True
        reason = "user"
    return DownloadSummary(
        active=len(rows),
        progress=progress,
        speed=speed,
        paused=paused,
        user_paused=downloads_held(db),
        pause_reason=reason,
    )


def _pause_reason(code: str | None) -> str | None:
    if code == "AUTH_BLOCKED":
        return "auth"
    if code == "USER_PAUSED":
        return "user"
    if code == "DISK_LOW":
        return "disk"
    return None


def _global_progress(rows: list[Download]) -> int:
    if not rows:
        return 0
    total = 0
    weight = 0
    for row in rows:
        size = row.size if row.size and row.size > 0 else 1
        total += row.progress * size
        weight += size
    if weight <= 0:
        return 0
    return max(0, min(100, int(total / weight)))


def _queue(db: Session) -> QueueSummary:
    rows = db.execute(
        select(Download, Media)
        .join(MediaItem, MediaItem.id == Download.media_item_id)
        .join(Media, Media.id == MediaItem.media_id)
        .where(Download.state.in_(_QUEUE))
        .order_by(Download.priority.desc(), Download.id)
    ).all()
    items = [
        QueueItem(id=download.id, state=download.state, title=media.title, progress=download.progress)
        for download, media in rows[:_QUEUE_LIMIT]
    ]
    return QueueSummary(count=len(rows), items=items)


def _to_watch(db: Session) -> WatchSummary:
    from mediatracker.core.to_watch import to_watch_pairs

    rows = to_watch_pairs(db, limit=None)
    episodes = 0
    films = 0
    items: list[WatchItem] = []
    for item, media in rows:
        film = media.type == "film" or item.season is None
        if film:
            films += 1
        else:
            episodes += 1
        if len(items) >= _WATCH_LIMIT:
            continue
        items.append(
            WatchItem(
                media_title=_shown(media.title),
                media_type=media.type,
                label=_label(item.season, item.episode, film=film),
                title=_shown(item.title) or None,
            )
        )
    return WatchSummary(episodes=episodes, films=films, items=items)


def _upcoming(db: Session) -> list[UpcomingItem]:
    today = date.today()
    end = today + timedelta(days=7)
    rows = db.execute(
        select(MediaItem, Media)
        .join(Media, Media.id == MediaItem.media_id)
        .where(MediaItem.air_date.is_not(None), MediaItem.air_date >= today, MediaItem.air_date < end)
        .order_by(MediaItem.air_date, MediaItem.season, MediaItem.episode)
        .limit(_UPCOMING_LIMIT)
    ).all()
    seen = {(item.media_id, item.season, item.episode) for item, _media in rows}
    items = [
        UpcomingItem(
            media_title=_shown(media.title),
            label=_label(item.season, item.episode, film=media.type == "film"),
            title=_shown(item.title) or None,
            air_date=item.air_date,
        )
        for item, media in rows
        if item.air_date is not None
    ]
    announced = db.scalars(
        select(Media).where(
            Media.next_air_date.is_not(None),
            Media.next_air_date >= today,
            Media.next_air_date < end,
        )
    ).all()
    for media in announced:
        key = (media.id, media.next_episode_season, media.next_episode_number)
        if key in seen or media.next_air_date is None:
            continue
        items.append(
            UpcomingItem(
                media_title=_shown(media.title),
                label=_label(media.next_episode_season, media.next_episode_number, film=media.type == "film"),
                title=_shown(media.next_episode_title) or None,
                air_date=media.next_air_date,
            )
        )
    items.sort(key=lambda row: (row.air_date, row.media_title))
    return items[:_UPCOMING_LIMIT]


def _catalog(db: Session, settings) -> CatalogSummary:
    from mediatracker.core.catalog_source_visibility import visible_latest_source_types

    visible = visible_latest_source_types(db, settings)
    if not visible:
        return CatalogSummary(films=0, series=0, latest=[])
    films = _catalog_count(db, "film", visible)
    series = _catalog_count(db, "series", visible)
    latest_rows = db.scalars(
        select(CatalogEntry)
        .where(CatalogEntry.ignored.is_(False), CatalogEntry.source.in_(visible))
        .order_by(CatalogEntry.first_seen_at.desc(), CatalogEntry.id.desc())
        .limit(_LATEST)
    ).all()
    latest = []
    for row in latest_rows:
        title, version = release_caption(
            row.title,
            quality=row.quality,
            language=row.language,
            season=row.season,
        )
        latest.append(
            CatalogLatest(
                title=title,
                year=row.year,
                media_type=row.media_type,
                poster_url=_poster(row.poster_url),
                version=version,
            )
        )
    return CatalogSummary(films=films, series=series, latest=latest)


def _catalog_count(db: Session, media_type: str, visible_sources: list[str]) -> int:
    value = db.scalar(
        select(func.count())
        .select_from(CatalogEntry)
        .where(
            CatalogEntry.media_type == media_type,
            CatalogEntry.ignored.is_(False),
            CatalogEntry.source.in_(visible_sources),
        )
    )
    return int(value or 0)


def _source_scan(db: Session) -> SourceScanSummary:
    media = db.scalar(
        select(Media)
        .where(Media.last_source_poll_at.is_not(None))
        .order_by(Media.last_source_poll_at.desc(), Media.id.desc())
    )
    if media is None:
        return SourceScanSummary(at=None, media_title=None, result=None, message=None)
    return SourceScanSummary(
        at=_trim(media.last_source_poll_at),
        media_title=_shown(media.title),
        result=media.last_poll_result,
        message=media.last_poll_message,
    )


def _health(db: Session, settings) -> HealthSummary:
    findings: list[tuple[str, str, str]] = []
    for row in db.scalars(select(Provider).order_by(Provider.kind, Provider.type, Provider.id)).all():
        status = row.health_status or ""
        if status not in _HEALTH_RANK:
            continue
        findings.append((row.type, status, row.health_message or ""))
    disk = _disk(db, settings)
    limit = threshold_bytes(db, settings)
    if disk.free_bytes is None:
        findings.append(("disk", "down", "Espace disque illisible."))
    elif limit and disk.free_bytes < limit:
        findings.append(("disk", "down", f"{disk.free_gb} Gio libres, sous le seuil."))
    if _volumes_split(db, settings):
        findings.append(("volumes", "degraded", "Les dossiers médias ne sont pas sur le même disque."))
    if not findings:
        return HealthSummary(status="ok", label=_HEALTH_LABEL["ok"], component=None, message=None)
    findings.sort(key=lambda item: _HEALTH_RANK.get(item[1], 0), reverse=True)
    name, status, message = findings[0]
    return HealthSummary(
        status=status,
        label=_HEALTH_LABEL.get(status, status),
        component=name,
        message=message or None,
    )


def _volumes_split(db: Session, settings) -> bool:
    layout = storage_layout(db, settings)
    ids = [device_id(path) for path in (layout.movies_dir, layout.series_dir, layout.downloads_dir, layout.trash_dir)]
    if any(value is None for value in ids):
        return False
    return len(set(ids)) > 1


def _disk(db: Session, settings) -> DiskSummary:
    target = incomplete_dir(storage_layout(db, settings))
    free = free_bytes(target, None)
    if free is None:
        return DiskSummary(free_bytes=None, free_gb=None)
    return DiskSummary(free_bytes=free, free_gb=round(free / _GIB, 1))


def _library_scan_at(db: Session) -> datetime | None:
    from mediatracker.db.models import Setting

    row = db.get(Setting, LAST_SCAN_KEY)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _label(season: int | None, episode: int | None, *, film: bool) -> str:
    if film or season is None or episode is None:
        return "Film"
    return f"S{season:02d}E{episode:02d}"


def _trim(value: datetime | None) -> datetime | None:
    """UTC instant with whole seconds, so the JSON has no fractional part."""
    if value is None:
        return None
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0)


def _releases(db: Session, kind: str | None) -> list[DigitalReleaseOut]:
    return [
        DigitalReleaseOut(
            title=row["title"],
            poster_url=_poster(row["poster_url"]),
            overview=row.get("overview"),
            date=row["date"],
            type=row["type"],
            tmdb_id=row["tmdb_id"],
            tracked=row["tracked"],
            media_id=row.get("media_id"),
        )
        for row in list_digital_releases(db)
        if kind is None or row["type"] == kind
    ]


def _shown(value: str | None) -> str:
    from mediatracker.core.display_title import clean_display_title

    return clean_display_title(value) or (value or "").strip()


def _poster(url: str | None) -> str | None:
    if isinstance(url, str) and url.startswith(f"{TMDB_IMAGE_BASE}/"):
        return url
    return None


def _downloader(db: Session, request: Request):
    from mediatracker.core.pipeline import _downloader as load

    _row, client = load(db, request.app.state.settings, None)
    return client
