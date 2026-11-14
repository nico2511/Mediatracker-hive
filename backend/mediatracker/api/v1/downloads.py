"""Download queue. The direct AllDebrid URL is never accepted or returned."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.pipeline import cancel_job, french_error, queue_pause, retry_job, schedule_full_retries
from mediatracker.core.pipeline.download_guard import (
    DOWNLOAD_TERMINAL_STATES,
    active_download_for_item,
)
from mediatracker.core.queue_hold import downloads_held, set_downloads_held
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider

router = APIRouter(prefix="/downloads", tags=["downloads"])

_TERMINAL = tuple(DOWNLOAD_TERMINAL_STATES)
_CHECK = {
    "LINK_DOWN": "hors ligne",
    "ALREADY_LOCAL": "déjà présent",
    "ALREADY_PRESENT": "déjà présent",
}


def _check_label(code: str | None) -> str | None:
    if not code:
        return None
    return _CHECK.get(code)


def _step_label(state: str) -> str | None:
    return {
        "pending": "En attente",
        "resolving": "Résolution du lien",
        "queued": "En file aria2",
        "downloading": "Téléchargement aria2",
        "post_processing": "Rangement",
    }.get(state)


def _public_hoster(hoster: str | None) -> str | None:
    if not hoster or hoster.casefold() == "uptobox":
        return None
    return hoster


class DownloadCreate(BaseModel):
    media_item_id: int | None = None
    link_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=120)


class DownloadOut(BaseModel):
    id: int
    media_id: int
    media_title: str
    media_item_id: int
    season: int | None
    episode: int | None
    link_id: int | None
    hoster: str | None
    source: str | None = None
    state: str
    step: str | None = None
    progress: int
    done_bytes: int
    size: int | None
    speed: int | None
    eta_seconds: int | None
    filename: str | None
    release: str | None = None
    error_code: str | None
    error: str | None
    check: str | None
    attempts: int
    next_retry_at: datetime | None
    created_at: datetime
    finished_at: datetime | None


class DownloadPage(BaseModel):
    items: list[DownloadOut]
    next_cursor: str | None = None
    queue_paused: bool
    queue_held: bool = False
    queue_code: str | None
    queue_message: str | None


def _cursor(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", "Curseur invalide.") from exc
    if parsed < 0:
        raise Problem(422, "Requête invalide", "Curseur invalide.")
    return parsed


def _source_label(provider_type: str | None, hoster: str | None) -> str | None:
    if provider_type == "comet":
        return "Comet"
    if provider_type == "wawacity":
        return f"Wawacity ({hoster})" if hoster else "Wawacity"
    if provider_type == "manual":
        return f"Manuel ({hoster})" if hoster else "Manuel"
    return hoster


def _release_name(filename: str | None) -> str | None:
    name = (filename or "").strip()
    if not name or "." not in name:
        return None
    return name


def _out(
    row: Download,
    item: MediaItem,
    media: Media,
    hoster: str | None,
    source: str | None,
    release: str | None,
) -> DownloadOut:
    # A local path may sit in ``error`` during post-processing. Do not show it as a failure.
    error = None if row.state == "post_processing" and (row.error or "").startswith("/") else row.error
    if error or row.error_code:
        error = french_error(row.error_code, error)
    check = _check_label(row.error_code)
    if check and error and check.casefold() in error.casefold():
        check = None
    return DownloadOut(
        id=row.id,
        media_id=media.id,
        media_title=media.title,
        media_item_id=item.id,
        season=item.season,
        episode=item.episode,
        link_id=row.link_id,
        hoster=hoster,
        source=source,
        state=row.state,
        step=_step_label(row.state),
        progress=row.progress,
        done_bytes=row.done_bytes,
        size=row.size,
        speed=row.speed,
        eta_seconds=row.eta_seconds,
        filename=row.filename,
        release=release,
        error_code=row.error_code,
        error=error,
        check=check,
        attempts=row.attempts,
        next_retry_at=row.next_retry_at,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _page(db: Session, rows: list[Download]) -> list[DownloadOut]:
    item_ids = {row.media_item_id for row in rows}
    items_by_id = (
        {item.id: item for item in db.scalars(select(MediaItem).where(MediaItem.id.in_(item_ids))).all()}
        if item_ids
        else {}
    )
    media_ids = {item.media_id for item in items_by_id.values()}
    media_by_id = (
        {media.id: media for media in db.scalars(select(Media).where(Media.id.in_(media_ids))).all()}
        if media_ids
        else {}
    )
    link_ids = {row.link_id for row in rows if row.link_id is not None}
    links_by_id = (
        {link.id: link for link in db.scalars(select(Link).where(Link.id.in_(link_ids))).all()} if link_ids else {}
    )
    provider_ids = {link.provider_id for link in links_by_id.values()}
    providers_by_id = (
        {provider.id: provider for provider in db.scalars(select(Provider).where(Provider.id.in_(provider_ids))).all()}
        if provider_ids
        else {}
    )
    items = []
    for row in rows:
        item = items_by_id.get(row.media_item_id)
        media = media_by_id.get(item.media_id) if item is not None else None
        if item is None or media is None:
            continue
        hoster = None
        source = None
        release = None
        if row.link_id is not None:
            link = links_by_id.get(row.link_id)
            if link is not None:
                hoster = _public_hoster(link.hoster)
                provider = providers_by_id.get(link.provider_id)
                source = _source_label(provider.type if provider is not None else None, hoster)
                release = _release_name(link.filename)
        items.append(_out(row, item, media, hoster, source, release))
    return items


def _pause_fields(db: Session) -> tuple[bool, str | None, str | None]:
    return queue_pause(db)


@router.get("", response_model=DownloadPage)
def list_downloads(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    state: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> DownloadPage:
    del _principal
    stmt = select(Download).where(Download.dismissed.is_(False))
    if state is not None:
        stmt = stmt.where(Download.state == state)
    if cursor is not None:
        stmt = stmt.where(Download.id < _cursor(cursor))
    rows = list(db.scalars(stmt.order_by(Download.id.desc()).limit(limit + 1)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    paused, code, message = _pause_fields(db)
    return DownloadPage(
        items=_page(db, rows),
        next_cursor=next_cursor,
        queue_paused=paused,
        queue_held=downloads_held(db),
        queue_code=code,
        queue_message=message,
    )


@router.post("", response_model=DownloadOut, status_code=201)
def create_download(
    body: DownloadCreate,
    response: Response,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> DownloadOut:
    del _principal
    key = (idempotency_key or body.idempotency_key or "").strip() or None
    if key and len(key) > 120:
        raise Problem(422, "Requête invalide", "La clé d'idempotence dépasse 120 caractères.")
    if key:
        existing = db.scalar(select(Download).where(Download.idempotency_key == key))
        if existing is not None:
            response.status_code = 200
            return _one(db, existing)
    item, link = _targets(db, body)
    if active_download_for_item(db, item.id) is not None:
        raise Problem(409, "Conflit", "Un téléchargement est déjà en cours pour cet épisode.")
    row = Download(
        media_item_id=item.id,
        link_id=link.id if link is not None else None,
        state="pending",
        idempotency_key=key,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if key:
            existing = db.scalar(select(Download).where(Download.idempotency_key == key))
            if existing is not None:
                return _one(db, existing)
        raise Problem(409, "Conflit", "Téléchargement déjà créé.") from exc
    db.refresh(row)
    return _one(db, row)


class RetryBatchOut(BaseModel):
    scheduled: int


@router.post("/retry-failed", response_model=RetryBatchOut)
def retry_failed_downloads(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RetryBatchOut:
    """Full retry of every failed job, spaced by the source delay."""
    del _principal
    rows = list(db.scalars(select(Download).where(Download.state == "failed")).all())
    scheduled = schedule_full_retries(db, rows, datetime.now(timezone.utc), request.app.state.settings)
    db.commit()
    return RetryBatchOut(scheduled=scheduled)


class QueueHoldOut(BaseModel):
    queue_paused: bool
    queue_held: bool
    queue_code: str | None
    queue_message: str | None


def _hold_out(db: Session) -> QueueHoldOut:
    paused, code, message = _pause_fields(db)
    return QueueHoldOut(
        queue_paused=paused,
        queue_held=downloads_held(db),
        queue_code=code,
        queue_message=message,
    )


@router.post("/pause", response_model=QueueHoldOut)
def pause_downloads(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> QueueHoldOut:
    """Pause the whole queue. Active aria2 handles are paused too."""
    del _principal
    set_downloads_held(db, True, downloader=_downloader(db, request))
    return _hold_out(db)


@router.post("/resume", response_model=QueueHoldOut)
def resume_downloads(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> QueueHoldOut:
    """Resume the queue after a user pause. An AllDebrid block is unchanged."""
    del _principal
    set_downloads_held(db, False, downloader=_downloader(db, request))
    return _hold_out(db)


@router.get("/{download_id}", response_model=DownloadOut)
def get_download(
    download_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> DownloadOut:
    del _principal
    return _one(db, _get(db, download_id))


@router.post("/{download_id}/retry", response_model=DownloadOut)
def retry_download(
    download_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> DownloadOut:
    del _principal
    row = _get(db, download_id)
    try:
        retry_job(db, row, request.app.state.settings)
    except RuntimeError as exc:
        raise Problem(409, "Conflit", "Seuls les échecs et les annulations peuvent être relancés.") from exc
    db.commit()
    db.refresh(row)
    return _one(db, row)


@router.post("/{download_id}/retry-all", response_model=DownloadOut)
def retry_download_from_scratch(
    download_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> DownloadOut:
    """Fresh Wawacity discovery, then every hoster in the configured order."""
    del _principal
    row = _get(db, download_id)
    scheduled = schedule_full_retries(db, [row], datetime.now(timezone.utc), request.app.state.settings)
    if scheduled == 0:
        raise Problem(409, "Conflit", "Seuls les échecs peuvent être relancés depuis le début.")
    db.commit()
    db.refresh(row)
    return _one(db, row)


@router.post("/{download_id}/cancel", response_model=DownloadOut)
def cancel_download(
    download_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> DownloadOut:
    del _principal
    row = _get(db, download_id)
    downloader = _downloader(db, request)
    try:
        cancel_job(db, row, downloader, now=datetime.now(timezone.utc), settings=request.app.state.settings)
    except RuntimeError as exc:
        raise Problem(409, "Conflit", "L'import en cours ne peut pas être annulé.") from exc
    db.commit()
    db.refresh(row)
    return _one(db, row)


class ClearOut(BaseModel):
    removed: int


@router.post("/clear-completed", response_model=ClearOut)
def clear_completed(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> ClearOut:
    """Drop finished rows from the list. Files already in the library stay."""
    del _principal
    rows = list(db.scalars(select(Download).where(Download.state == "done")).all())
    for row in rows:
        db.delete(row)
    db.commit()
    return ClearOut(removed=len(rows))


@router.delete("/{download_id}", status_code=204)
def delete_download(
    download_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> Response:
    del _principal
    row = _get(db, download_id)
    if row.state in {"pending", "resolving", "queued", "downloading"}:
        raise Problem(409, "En cours", "Ce téléchargement est encore actif.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


def _targets(db: Session, body: DownloadCreate) -> tuple[MediaItem, Link | None]:
    link = None
    if body.link_id is not None:
        link = db.get(Link, body.link_id)
        if link is None:
            raise Problem(404, "Introuvable", "Lien introuvable.")
        if body.media_item_id is not None and body.media_item_id != link.media_item_id:
            raise Problem(422, "Requête invalide", "Le lien n'appartient pas à cet épisode.")
        item = db.get(MediaItem, link.media_item_id)
    elif body.media_item_id is not None:
        item = db.get(MediaItem, body.media_item_id)
    else:
        raise Problem(422, "Requête invalide", "Indiquez un épisode ou un lien.")
    if item is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    return item, link


def _get(db: Session, download_id: int) -> Download:
    row = db.get(Download, download_id)
    if row is None:
        raise Problem(404, "Introuvable", "Téléchargement introuvable.")
    return row


def _one(db: Session, row: Download) -> DownloadOut:
    found = _page(db, [row])
    if not found:
        raise Problem(404, "Introuvable", "Téléchargement introuvable.")
    return found[0]


def _downloader(db: Session, request: Request):
    from mediatracker.core.pipeline import _downloader as load

    _row, client = load(db, request.app.state.settings, None)
    return client
