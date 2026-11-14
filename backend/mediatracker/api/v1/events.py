"""Read the outbox. Delivery to ntfy, Home Assistant and HMAC webhooks is a later milestone."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.v1.downloads import _page, _pause_fields
from mediatracker.core.queue_hold import downloads_held
from mediatracker.db.models import Download
from mediatracker.events.catalog import EVENT_TYPES, NOTIFY_DEFAULT_OFF, NOTIFY_EVENTS
from mediatracker.events.journal import event_out, list_journal_events, validate_search

router = APIRouter(prefix="/events", tags=["events"])


class EventOut(BaseModel):
    id: int
    ts: datetime
    last_seen_at: datetime
    repeat_count: int = 1
    type: str
    level: str
    media_id: int | None
    item_id: int | None
    label: str
    payload: dict[str, Any]


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None = None


class EventCatalog(BaseModel):
    events: list[str]


class NotifyEventOut(BaseModel):
    type: str
    label: str
    default_on: bool = True


class NotifyCatalog(BaseModel):
    events: list[NotifyEventOut]


@router.get("/stream")
def stream_progress(
    request: Request,
    _principal=Depends(require_scope("read")),
    media_id: int | None = Query(default=None, ge=1),
) -> StreamingResponse:
    """SSE progress for downloads and open fiches. One shared snapshot per second for all clients."""
    del _principal
    from mediatracker.api.v1.progress_stream import format_progress_event, hub_for_app

    hub = hub_for_app(request.app)
    limit = getattr(request.app.state, "sse_limit", None)
    sub = hub.subscribe(media_id, limit=limit)

    def generate():
        try:
            while True:
                payload = hub.wait_payload(sub, timeout=30.0)
                if payload is None:
                    continue
                yield format_progress_event(payload)
                sub.sent += 1
                if sub.limit is not None and sub.sent >= sub.limit:
                    break
        finally:
            hub.unsubscribe(sub)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def progress_snapshot_payload(db: Session) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(Download).where(Download.state.not_in(("done", "cancelled"))).order_by(Download.id.desc()).limit(50)
        ).all()
    )
    paused, code, message = _pause_fields(db)
    items = [item.model_dump(mode="json") for item in _page(db, rows)]
    return {
        "queue_paused": paused,
        "queue_held": downloads_held(db),
        "queue_code": code,
        "queue_message": message,
        "items": items,
        "downloads": items,
    }


@router.get("/types", response_model=EventCatalog)
def list_event_types(_principal=Depends(require_scope("read"))) -> EventCatalog:
    """Catalogue des types d'événements SSE (admin / intégration, sans écran dédié dans l'UI)."""
    del _principal
    return EventCatalog(events=sorted(EVENT_TYPES))


@router.get("/notifications", response_model=NotifyCatalog)
def list_notification_events(_principal=Depends(require_scope("read"))) -> NotifyCatalog:
    del _principal
    return NotifyCatalog(
        events=[
            NotifyEventOut(type=key, label=label, default_on=key not in NOTIFY_DEFAULT_OFF)
            for key, label in NOTIFY_EVENTS
        ]
    )


@router.get("", response_model=EventPage)
def list_events(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    event_type: str | None = Query(default=None, alias="type"),
    category: str | None = None,
    media_id: int | None = None,
    download_id: int | None = None,
    level: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = Query(default=None, description="Recherche sur le libellé et le type."),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> EventPage:
    del _principal
    rows, next_cursor = list_journal_events(
        db,
        limit=limit,
        cursor=cursor,
        event_type=event_type,
        category=category,
        media_id=media_id,
        download_id=download_id,
        level=level,
        since=since,
        until=until,
        search=validate_search(q),
    )
    return EventPage(
        items=[EventOut(**event_out(row)) for row in rows],
        next_cursor=next_cursor,
    )
