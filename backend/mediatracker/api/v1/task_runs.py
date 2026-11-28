"""Lecture des passages task_run et interrupteur de collecte."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.db.models import TaskRun
from mediatracker.metro.collection import collection_status, set_collection_duration

router = APIRouter(prefix="/task-runs", tags=["task-runs"])


class TaskRunOut(BaseModel):
    id: int
    line: str
    station: str
    sequence_id: str | None = None
    parent_run_id: int | None = None
    edge_id: str | None = None
    trigger: str
    cause: str | None = None
    subject_ref: str | None = None
    media_id: int | None = None
    blocked_by: str | None = None
    waiting_on: str | None = None
    outcome: str
    started_at: datetime
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    duration_ms: int | None = None


class TaskRunListOut(BaseModel):
    hours: float
    station: str | None = None
    line: str | None = None
    count: int
    items: list[TaskRunOut] = Field(default_factory=list)


class TaskRunCollectionOut(BaseModel):
    active: bool
    until: str | None = None
    mode: str
    duration: Literal["off", "1h", "6h", "24h", "unlimited", "timed"] | None = None


class TaskRunCollectionPatch(BaseModel):
    duration: Literal["off", "1h", "6h", "24h", "unlimited"]


@router.get("", response_model=TaskRunListOut)
def list_task_runs(
    db: Session = Depends(get_db),
    _user=Depends(require_scope("read")),
    hours: float = Query(24.0, ge=1.0, le=168.0),
    station: str | None = Query(None, max_length=8),
    line: str | None = Query(None, max_length=32),
    limit: int = Query(500, ge=1, le=2000),
) -> TaskRunListOut:
    moment = datetime.now(timezone.utc)
    since = moment - timedelta(hours=hours)
    query = select(TaskRun).where(TaskRun.started_at >= since).order_by(TaskRun.started_at.desc()).limit(limit)
    if station:
        query = query.where(TaskRun.station == station.upper())
    if line:
        query = query.where(TaskRun.line == line)
    rows = db.scalars(query).all()
    items = [
        TaskRunOut(
            id=row.id,
            line=row.line,
            station=row.station,
            sequence_id=row.sequence_id,
            parent_run_id=row.parent_run_id,
            edge_id=row.edge_id,
            trigger=row.trigger,
            cause=row.cause,
            subject_ref=row.subject_ref,
            media_id=row.media_id,
            blocked_by=row.blocked_by,
            waiting_on=row.waiting_on,
            outcome=row.outcome,
            started_at=row.started_at,
            finished_at=row.finished_at,
            heartbeat_at=row.heartbeat_at,
            duration_ms=row.duration_ms,
        )
        for row in rows
    ]
    return TaskRunListOut(hours=hours, station=station, line=line, count=len(items), items=items)


@router.get("/collection", response_model=TaskRunCollectionOut)
def get_task_run_collection(
    db: Session = Depends(get_db),
    _user=Depends(require_scope("read")),
) -> TaskRunCollectionOut:
    body = collection_status(db)
    return TaskRunCollectionOut(**body)


@router.patch("/collection", response_model=TaskRunCollectionOut)
def patch_task_run_collection(
    body: TaskRunCollectionPatch,
    db: Session = Depends(get_db),
    _user=Depends(require_scope("admin")),
) -> TaskRunCollectionOut:
    result = set_collection_duration(db, body.duration)  # type: ignore[arg-type]
    db.commit()
    return TaskRunCollectionOut(**result)
