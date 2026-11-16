from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.download_sources_status import download_source_statuses
from mediatracker.core.source_probe import parse_probe_query, probe_download_sources
from mediatracker.settings_store import SettingsError, list_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingOut(BaseModel):
    key: str
    value: Any = None
    secret: bool
    set: bool


class SettingsOut(BaseModel):
    settings: list[SettingOut]


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class SourceProbeIn(BaseModel):
    query: str
    tmdb_id: int | None = None
    imdb_id: str | None = None
    sources: list[str] | None = None


class HosterOptionsOut(BaseModel):
    hosters: list[str]


class SourceProbeItemOut(BaseModel):
    source: str
    label: str
    issue: str
    link_count: int
    reason: str | None
    duration_ms: int


class SourceProbeOut(BaseModel):
    results: list[SourceProbeItemOut]
    profile_name: str | None = None


class DownloadSourceStatusOut(BaseModel):
    source: str
    label: str
    enabled: bool
    available: bool = True
    rank: int
    state: str | None = None
    message: str = ""
    since: datetime | None = None
    last_probe_at: datetime | None = None
    last_probe_link_count: int | None = None
    last_probe_duration_ms: int | None = None
    last_probe_issue: str | None = None
    last_probe_reason: str | None = None
    last_success_at: datetime | None = None


class DownloadSourcesStatusOut(BaseModel):
    sources: list[DownloadSourceStatusOut]
    available_sources: list[str] = []
    download_ready: bool = True
    blocked_message: str | None = None


@router.get("", response_model=SettingsOut)
def get_settings_route(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SettingsOut:
    del _principal
    items = list_settings(db, request.app.state.settings.secret_key)
    return SettingsOut(settings=[SettingOut.model_validate(item) for item in items])


@router.get("/sources/status", response_model=DownloadSourcesStatusOut)
def list_download_sources_status(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> DownloadSourcesStatusOut:
    del _principal
    bundle = download_source_statuses(db, request.app.state.settings)
    return DownloadSourcesStatusOut(
        sources=[DownloadSourceStatusOut.model_validate(row, from_attributes=True) for row in bundle.sources],
        available_sources=bundle.available_sources,
        download_ready=bundle.download_ready,
        blocked_message=bundle.blocked_message,
    )


@router.get("/sources/hosters", response_model=HosterOptionsOut)
def list_hoster_options(
    _request: Request,
    _db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> HosterOptionsOut:
    del _request, _db, _principal
    from mediatracker.integrations.alldebrid_public_hosts import list_hoster_picker_options

    return HosterOptionsOut(hosters=list_hoster_picker_options())


@router.post("/sources/probe", response_model=SourceProbeOut)
def probe_sources(
    body: SourceProbeIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SourceProbeOut:
    del _principal
    try:
        target = parse_probe_query(body.query, imdb_id=body.imdb_id, tmdb_id=body.tmdb_id)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc
    run = probe_download_sources(
        db,
        request.app.state.settings,
        target,
        sources=body.sources,
    )
    db.commit()
    return SourceProbeOut(
        results=[
            SourceProbeItemOut(
                source=row.source,
                label=row.label,
                issue=row.issue,
                link_count=row.link_count,
                reason=row.reason,
                duration_ms=row.duration_ms,
            )
            for row in run.results
        ],
        profile_name=run.profile_name,
    )


@router.patch("", response_model=SettingsOut)
def patch_settings(
    body: SettingsPatch,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> SettingsOut:
    del _principal
    try:
        update_settings(db, request.app.state.settings, body.values)
    except SettingsError as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc
    from mediatracker.hive.transfer_runtime import after_hive_transfer_settings_patch

    after_hive_transfer_settings_patch(request, db, set(body.values))
    db.commit()
    items = list_settings(db, request.app.state.settings.secret_key)
    return SettingsOut(settings=[SettingOut.model_validate(item) for item in items])
