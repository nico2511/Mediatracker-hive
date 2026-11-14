from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.build_info import app_version, build_date, short_sha
from mediatracker.core.disk_usage import needs_refresh, schedule, snapshot
from mediatracker.core.source_state import current_states, recent_changes
from mediatracker.health_report import Component, components, database_ok

router = APIRouter(tags=["health"])


class ApiHealthOut(BaseModel):
    status: str
    database: str
    version: str
    sha: str | None = None
    build_date: str | None = None


class ComponentOut(BaseModel):
    name: str
    status: str
    message: str


class ComponentsOut(BaseModel):
    status: str
    components: list[ComponentOut]


class DiskFileOut(BaseModel):
    name: str
    written_bytes: int
    reserved_bytes: int
    job_id: int | None = None
    job_state: str = ""
    orphan: bool = True


class DiskFolderOut(BaseModel):
    key: str
    label: str
    path: str
    bytes: int
    written_bytes: int = 0
    reserved_bytes: int = 0
    missing: bool
    device: str
    mount: str
    files: list[DiskFileOut] = Field(default_factory=list)


class DiskVolumeOut(BaseModel):
    device: str
    mount: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    measured_bytes: int
    other_bytes: int
    folders: list[str]


class DiskUsageOut(BaseModel):
    computed_at: datetime | None = None
    running: bool = False
    error: str = ""
    free_bytes: int | None = None
    folders: list[DiskFolderOut] = Field(default_factory=list)
    volumes: list[DiskVolumeOut] = Field(default_factory=list)


class ProcessMetricsOut(BaseModel):
    pid: int | None = None
    cpu_percent: float | None = None
    rss_bytes: int | None = None
    write_bytes: int | None = None


class Aria2MetricsOut(BaseModel):
    download_speed: int | None = None
    upload_speed: int | None = None
    num_active: int | None = None
    num_waiting: int | None = None
    notify_mode: str | None = None


class TaskDurationOut(BaseModel):
    count: int
    last_s: float | None = None
    avg_s: float | None = None
    max_s: float | None = None


class SourceRequestOut(BaseModel):
    count: int
    errors: int


class MetricsInstantOut(BaseModel):
    processes: dict[str, ProcessMetricsOut]
    aria2: Aria2MetricsOut
    worker_wakes: dict[str, int] = Field(default_factory=dict)
    task_durations: dict[str, TaskDurationOut] = Field(default_factory=dict)
    source_requests: dict[str, SourceRequestOut] = Field(default_factory=dict)


class RouteHttpMetricsOut(BaseModel):
    count: int
    mean_ms: float | None = None
    p95_ms: float | None = None


class MetricsOut(BaseModel):
    sampled_at: float
    interval_s: float
    instant: MetricsInstantOut
    series: list[dict[str, Any]]
    http: dict[str, RouteHttpMetricsOut]


class MetricsHistoryPointOut(BaseModel):
    bucket_at: datetime
    cpu_api: float | None = None
    cpu_worker: float | None = None
    rss_api: int | None = None
    rss_worker: int | None = None
    worker_wakes: dict[str, int] = Field(default_factory=dict)
    source_requests: dict[str, SourceRequestOut] = Field(default_factory=dict)
    task_durations: dict[str, TaskDurationOut] = Field(default_factory=dict)


class MetricsHistoryOut(BaseModel):
    since: datetime
    until: datetime
    bucket_minutes: int
    points: list[MetricsHistoryPointOut]


class SourceStateOut(BaseModel):
    key: str
    kind: str
    label: str
    state: str
    message: str = ""
    since: datetime
    last_debrid_at: datetime | None = None


class SourceChangeOut(BaseModel):
    key: str
    kind: str
    label: str
    state: str
    message: str = ""
    changed_at: datetime


class SourceStatesOut(BaseModel):
    targets: list[SourceStateOut]
    changes: list[SourceChangeOut]


@router.get("/health", response_model=ApiHealthOut)
def api_health(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> ApiHealthOut:
    """Version, commit et état de la base. Session ou jeton avec portée lecture."""
    del _principal
    try:
        database_ok(db)
    except Exception as exc:
        raise Problem(503, "Indisponible", "La base de données ne répond pas.") from exc
    return ApiHealthOut(
        status="ok",
        database="ok",
        version=app_version(),
        sha=short_sha(),
        build_date=build_date(),
    )


@router.get("/health/components", response_model=ComponentsOut)
def api_components(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> ComponentsOut:
    del _principal
    rows = components(db, request.app.state.settings)
    overall = _overall(rows)
    return ComponentsOut(
        status=overall,
        components=[ComponentOut(name=row.name, status=row.status, message=row.message) for row in rows],
    )


@router.get("/health/disk", response_model=DiskUsageOut)
def api_disk_usage(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> DiskUsageOut:
    """Cached folder sizes. A missing or old cache starts a background pass and returns at once."""
    del _principal
    report = snapshot(db)
    if needs_refresh(report):
        schedule(request.app.state.session_factory, request.app.state.settings, force=False)
        report = snapshot(db)
    return _disk_out(report)


@router.get("/health/metrics", response_model=MetricsOut)
def api_metrics(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> MetricsOut:
    """Instant process and aria2 metrics plus an in-memory time series. Session required."""
    del _principal
    collector = request.app.state.metrics
    body = collector.snapshot(db, request.app.state.settings)
    return MetricsOut(
        sampled_at=body["sampled_at"],
        interval_s=body["interval_s"],
        instant=MetricsInstantOut(
            processes={key: ProcessMetricsOut(**value) for key, value in body["instant"]["processes"].items()},
            aria2=Aria2MetricsOut(**body["instant"]["aria2"]),
            worker_wakes=dict(body["instant"].get("worker_wakes") or {}),
            task_durations=_task_durations_out(body["instant"].get("task_durations") or {}),
            source_requests=_source_requests_out(body["instant"].get("source_requests") or {}),
        ),
        series=body["series"],
        http={route: RouteHttpMetricsOut(**stats) for route, stats in body["http"].items()},
    )


@router.get("/health/history", response_model=MetricsHistoryOut)
def api_metrics_history(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    hours: float = Query(168.0, ge=1.0, le=24.0 * 7),
) -> MetricsHistoryOut:
    """SQLite metrics history for the last ``hours`` (up to seven days). Session required."""
    del request, _principal
    from mediatracker.core.metrics.history import BUCKET_MINUTES, fetch_history

    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=hours)
    rows = fetch_history(db, since=since, until=until)
    return MetricsHistoryOut(
        since=since,
        until=until,
        bucket_minutes=BUCKET_MINUTES,
        points=[_history_point(row) for row in rows],
    )


@router.get("/health/history/export")
def api_metrics_history_export(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    hours: float = Query(168.0, ge=1.0, le=24.0 * 7),
):
    """Download metrics history as JSON. Session required."""
    del request, _principal
    from mediatracker.core.metrics.history import history_export_payload

    until = datetime.now(timezone.utc)
    since = until - timedelta(hours=hours)
    payload = history_export_payload(db, since=since, until=until)
    filename = f"metrics-history-{int(since.timestamp())}.json"
    return JSONResponse(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/health/sources", response_model=SourceStatesOut)
def api_health_sources(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SourceStatesOut:
    del _principal
    from mediatracker.core.source_state import last_successful_debrid_by_hoster

    last_debrid = last_successful_debrid_by_hoster(db)
    targets = [
        SourceStateOut(
            key=row.target_key,
            kind=row.kind,
            label=row.label,
            state=row.state,
            message=row.message or "",
            since=row.since,
            last_debrid_at=last_debrid.get(row.label.casefold()) if row.kind == "hoster" else None,
        )
        for row in current_states(db)
    ]
    changes = [
        SourceChangeOut(
            key=row.target_key,
            kind=row.kind,
            label=row.label,
            state=row.state,
            message=row.message or "",
            changed_at=row.changed_at,
        )
        for row in recent_changes(db)
    ]
    return SourceStatesOut(targets=targets, changes=changes)


@router.post("/health/disk", response_model=DiskUsageOut)
def api_disk_refresh(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> DiskUsageOut:
    """Ask for a new measurement. The walk keeps running after this response."""
    del _principal
    schedule(request.app.state.session_factory, request.app.state.settings, force=True)
    return _disk_out(snapshot(db))


def _disk_out(report: dict) -> DiskUsageOut:
    moment = report.get("computed_at")
    parsed = None
    if moment:
        parsed = datetime.fromisoformat(str(moment))
    return DiskUsageOut(
        computed_at=parsed,
        running=bool(report.get("running")),
        error=str(report.get("error") or ""),
        free_bytes=report.get("free_bytes"),
        folders=[DiskFolderOut(**row) for row in report.get("folders") or []],
        volumes=[DiskVolumeOut(**row) for row in report.get("volumes") or []],
    )


def _task_durations_out(raw: dict[str, Any]) -> dict[str, TaskDurationOut]:
    out: dict[str, TaskDurationOut] = {}
    for name, row in raw.items():
        if not isinstance(row, dict):
            continue
        out[name] = TaskDurationOut(
            count=int(row.get("count") or 0),
            last_s=row.get("last_s"),
            avg_s=row.get("avg_s"),
            max_s=row.get("max_s"),
        )
    return out


def _source_requests_out(raw: dict[str, Any]) -> dict[str, SourceRequestOut]:
    out: dict[str, SourceRequestOut] = {}
    for name, row in raw.items():
        if not isinstance(row, dict):
            continue
        out[name] = SourceRequestOut(
            count=int(row.get("count") or 0),
            errors=int(row.get("errors") or 0),
        )
    return out


def _history_point(row: dict[str, Any]) -> MetricsHistoryPointOut:
    moment = datetime.fromisoformat(str(row["bucket_at"]))
    return MetricsHistoryPointOut(
        bucket_at=moment,
        cpu_api=row.get("cpu_api"),
        cpu_worker=row.get("cpu_worker"),
        rss_api=row.get("rss_api"),
        rss_worker=row.get("rss_worker"),
        worker_wakes=dict(row.get("worker_wakes") or {}),
        source_requests=_source_requests_out(row.get("source_requests") or {}),
        task_durations=_task_durations_out(row.get("task_durations") or {}),
    )


def _overall(rows: list[Component]) -> str:
    statuses = {row.status for row in rows}
    if "down" in statuses:
        return "down"
    if "degraded" in statuses:
        return "degraded"
    return "ok"
