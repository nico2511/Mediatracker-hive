"""Latest-releases catalog: list, refresh, ignore, and follow."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.api.v1.media import MediaCreate, MediaOut, _to_media, insert_followed
from mediatracker.core.catalog import _filter_value, _int_setting, catalog_clock, fold_title, refresh_catalog
from mediatracker.core.catalog_list import (
    followed_index as _followed_index,
)
from mediatracker.core.catalog_list import (
    in_library as _in_library,
)
from mediatracker.core.catalog_list import (
    is_followed as _is_followed,
)
from mediatracker.core.catalog_list import (
    library_files as _library_files,
)
from mediatracker.core.catalog_list import (
    list_catalog_page,
)
from mediatracker.core.catalog_poster import display_poster_url, resolve_catalog_posters
from mediatracker.core.display_title import clean_display_title
from mediatracker.core.source_poll import close_source
from mediatracker.core.tracking import tmdb_page_url
from mediatracker.db.models import CatalogEntry, Media
from mediatracker.providers.sources.loading import sources_for_catalog

router = APIRouter(prefix="/catalog", tags=["catalog"])


class CatalogSourceOut(BaseModel):
    source: str
    url: str
    quality: str | None = None
    language: str | None = None


class CatalogEntryOut(BaseModel):
    id: int
    title: str
    year: int | None
    media_type: str
    season: int | None
    episode: int | None
    quality: str | None
    language: str | None
    source: str
    source_url: str
    sources: list[CatalogSourceOut]
    first_seen_at: datetime
    last_seen_at: datetime
    tmdb_id: int | None
    tmdb_url: str | None
    poster_url: str | None
    overview: str | None
    rating: float | None
    genres: list[str]
    ignored: bool
    followed: bool
    in_library: bool
    media_id: int | None = None


class CatalogPage(BaseModel):
    items: list[CatalogEntryOut]
    total: int
    last_refresh_at: datetime | None
    next_refresh_at: datetime | None
    last_error: str | None
    genres: list[str]
    years: list[int]
    news_sources_usable: bool = True
    notice: str | None = None


class CatalogStatus(BaseModel):
    last_refresh_at: datetime | None
    next_refresh_at: datetime | None
    last_error: str | None
    interval_hours: int
    retention_days: int
    quality: str
    language: str
    news_sources_usable: bool = True
    notice: str | None = None


class CatalogRefreshOut(BaseModel):
    added: int
    updated: int
    matched: int
    purged: int
    skipped: bool
    error: str | None
    refreshed_at: datetime | None
    next_refresh_at: datetime | None


class CatalogFollow(BaseModel):
    new_episode_policy: str | None = None
    seasons: list[int] | None = None
    start_now: bool = False


def _entry(
    row: CatalogEntry,
    *,
    followed: bool,
    in_library: bool,
    media_id: int | None = None,
) -> CatalogEntryOut:
    sources: list[CatalogSourceOut] = []
    for item in row.sources or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        sources.append(
            CatalogSourceOut(
                source=str(item.get("source") or row.source),
                url=str(item["url"]),
                quality=item.get("quality"),
                language=item.get("language"),
            )
        )
    genres = [str(name) for name in (row.genres or []) if name]
    return CatalogEntryOut(
        id=row.id,
        title=clean_display_title(row.title) or row.title,
        year=row.year,
        media_type=row.media_type,
        season=row.season,
        episode=row.episode,
        quality=row.quality,
        language=row.language,
        source=row.source,
        source_url=row.source_url,
        sources=sources,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        tmdb_id=row.tmdb_id,
        tmdb_url=tmdb_page_url(row.media_type, row.tmdb_id),
        poster_url=display_poster_url(row),
        overview=row.overview,
        rating=row.rating,
        genres=genres,
        ignored=row.ignored,
        followed=followed,
        in_library=in_library,
        media_id=media_id,
    )


@router.get("/status", response_model=CatalogStatus)
def catalog_status(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> CatalogStatus:
    del _principal
    settings = request.app.state.settings
    from mediatracker.core.catalog_source_visibility import (
        NOTICE_NO_LATEST_SOURCES,
        any_latest_catalog_usable,
    )

    last, nxt, error = catalog_clock(db)
    quality = _filter_value(db, "catalog_quality", settings.catalog_quality) or ""
    language = _filter_value(db, "catalog_language", settings.catalog_language) or ""
    usable = any_latest_catalog_usable(db, settings)
    return CatalogStatus(
        last_refresh_at=last,
        next_refresh_at=nxt,
        last_error=error,
        interval_hours=_int_setting(db, "catalog_interval_hours", settings.catalog_interval_hours),
        retention_days=_int_setting(db, "catalog_retention_days", settings.catalog_retention_days),
        quality=quality,
        language=language,
        news_sources_usable=usable,
        notice=None if usable else NOTICE_NO_LATEST_SOURCES,
    )


@router.get("", response_model=CatalogPage)
def list_catalog(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    media_type: str = Query(alias="type", pattern="^(film|series)$"),
    q: str | None = None,
    genre: str | None = None,
    year: int | None = Query(default=None, ge=1880, le=2100),
    followed: bool | None = None,
    in_library: bool | None = None,
    include_ignored: bool = False,
    limit: int = Query(default=60, ge=1, le=200),
) -> CatalogPage:
    del _principal
    settings = request.app.state.settings
    page = list_catalog_page(
        db,
        settings,
        media_type=media_type,
        q=q,
        genre=genre,
        year=year,
        followed=followed,
        in_library_flag=in_library,
        include_ignored=include_ignored,
        limit=limit,
    )
    resolve_catalog_posters(db, settings, page.rows)
    db.commit()
    by_tmdb, by_title = _followed_index(db)
    fiche_tmdb, fiche_title = _fiche_index(db)
    files = _library_files(db)
    from mediatracker.core.catalog_source_visibility import (
        NOTICE_NO_LATEST_SOURCES,
        any_latest_catalog_usable,
    )

    last, nxt, error = catalog_clock(db)
    usable = any_latest_catalog_usable(db, settings)
    return CatalogPage(
        items=[
            _entry(
                row,
                followed=_is_followed(row, by_tmdb, by_title),
                in_library=_in_library(row, files),
                media_id=_fiche_id(row, fiche_tmdb, fiche_title),
            )
            for row in page.rows
        ],
        total=page.total,
        last_refresh_at=last,
        next_refresh_at=nxt,
        last_error=error,
        genres=page.genres,
        years=page.years,
        news_sources_usable=usable,
        notice=None if usable else NOTICE_NO_LATEST_SOURCES,
    )


@router.post("/refresh", response_model=CatalogRefreshOut)
def refresh_now(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> CatalogRefreshOut:
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
    return CatalogRefreshOut(
        added=report.added,
        updated=report.updated,
        matched=report.matched,
        purged=report.purged,
        skipped=report.skipped,
        error=report.error,
        refreshed_at=report.refreshed_at,
        next_refresh_at=report.next_refresh_at,
    )


@router.post("/{entry_id}/ignore", response_model=CatalogEntryOut)
def ignore_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> CatalogEntryOut:
    del _principal
    row = _get(db, entry_id)
    row.ignored = True
    db.commit()
    db.refresh(row)
    return _one(db, row)


@router.post("/{entry_id}/restore", response_model=CatalogEntryOut)
def restore_entry(
    entry_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> CatalogEntryOut:
    del _principal
    row = _get(db, entry_id)
    row.ignored = False
    db.commit()
    db.refresh(row)
    return _one(db, row)


@router.post("/{entry_id}/follow", response_model=MediaOut, status_code=201)
def follow_entry(
    entry_id: int,
    body: CatalogFollow,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> MediaOut:
    del _principal
    row = _get(db, entry_id)
    _reject_if_followed(db, row)
    from mediatracker.core.follow import default_pref_language, default_pref_quality

    settings = request.app.state.settings
    policy = _policy(row.media_type, body.new_episode_policy)
    created, episodes = insert_followed(
        db,
        settings.secret_key,
        MediaCreate(
            type=row.media_type,
            title=row.title,
            year=row.year,
            tmdb_id=row.tmdb_id,
            overview=row.overview,
            poster_url=row.poster_url or row.source_poster_url,
            new_episode_policy=policy,
            pref_quality=row.quality or default_pref_quality(db),
            pref_language=row.language or default_pref_language(db),
            seasons=body.seasons,
            start_now=body.start_now,
        ),
        settings=settings,
    )
    from mediatracker.core.attach_deferred import commit_follow_side_effects, schedule_attach_known_files

    commit_follow_side_effects(db, settings)
    schedule_attach_known_files(request.app.state.session_factory, settings, created.id)
    created._attached = 0  # type: ignore[attr-defined]
    return _to_media(created, episodes, db)


def _reject_if_followed(db: Session, row: CatalogEntry) -> None:
    medias = list(db.scalars(select(Media).where(Media.type == row.media_type, Media.followed.is_(True))).all())
    folded = fold_title(row.title)
    for media in medias:
        same_tmdb = bool(row.tmdb_id and media.tmdb_id == row.tmdb_id)
        same_title = fold_title(media.title) == folded and media.year == row.year
        if same_tmdb or same_title:
            raise Problem(409, "Conflit", "Ce média est déjà suivi.")


def _policy(media_type: str, policy: str | None) -> str:
    if media_type == "series":
        if policy not in {"propose", "auto"}:
            raise Problem(
                422,
                "Requête invalide",
                "Choisissez « proposer » ou « automatique » pour une série.",
            )
        return policy
    if policy in {"propose", "auto"}:
        return policy
    return "propose"


def _get(db: Session, entry_id: int) -> CatalogEntry:
    row = db.get(CatalogEntry, entry_id)
    if row is None:
        raise Problem(404, "Introuvable", "Cette nouveauté est introuvable.")
    return row


def _fiche_index(db: Session) -> tuple[dict[tuple[str, int], int], dict[tuple[str, str, int | None], int]]:
    rows = list(db.scalars(select(Media).order_by(Media.id)).all())
    by_tmdb: dict[tuple[str, int], int] = {}
    by_title: dict[tuple[str, str, int | None], int] = {}
    for row in rows:
        if row.tmdb_id:
            by_tmdb.setdefault((row.type, row.tmdb_id), row.id)
        by_title.setdefault((row.type, fold_title(row.title), row.year), row.id)
    return by_tmdb, by_title


def _fiche_id(
    row: CatalogEntry,
    by_tmdb: dict[tuple[str, int], int],
    by_title: dict[tuple[str, str, int | None], int],
) -> int | None:
    if row.tmdb_id and (row.media_type, row.tmdb_id) in by_tmdb:
        return by_tmdb[(row.media_type, row.tmdb_id)]
    return by_title.get((row.media_type, fold_title(row.title), row.year))


def _one(db: Session, row: CatalogEntry) -> CatalogEntryOut:
    by_tmdb, by_title = _followed_index(db)
    fiche_tmdb, fiche_title = _fiche_index(db)
    files = _library_files(db)
    return _entry(
        row,
        followed=_is_followed(row, by_tmdb, by_title),
        in_library=_in_library(row, files),
        media_id=_fiche_id(row, fiche_tmdb, fiche_title),
    )
