"""Manual deletion, trash, and the auto-cleanup preview. Files move, they are not unlinked."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.cleanup import (
    CleanupError,
    delete_files,
    format_bytes,
    preview,
    purge_all,
    purge_one,
    reclaimed_bytes,
    restore,
)
from mediatracker.core.keep import MIGRATION_NOTICE_KEY, manual_trash_requires_confirm
from mediatracker.db.models import Media, MediaItem, Setting, TrashEntry

router = APIRouter(prefix="/cleanup", tags=["cleanup"])
trash_router = APIRouter(prefix="/trash", tags=["cleanup"])


class DeleteFilesIn(BaseModel):
    media_id: int = Field(ge=1)
    scope: str = Field(pattern="^(media|season|episode)$")
    season: int | None = Field(default=None, ge=0)
    item_id: int | None = Field(default=None, ge=1)
    confirm: bool = False


class TrashOut(BaseModel):
    id: int
    original_path: str
    label: str
    reason: str
    size: int
    deleted_at: datetime
    purge_after: datetime
    media_id: int | None
    season: int | None
    restored: bool
    purged: bool


class MissingFileOut(BaseModel):
    path: str
    message: str


class DeleteResult(BaseModel):
    moved: int
    bytes: int
    entries: list[TrashOut]
    missing: list[MissingFileOut] = Field(default_factory=list)


class PreviewFile(BaseModel):
    media_id: int
    title: str
    season: int | None
    episode: int | None
    path: str
    bytes: int
    delete_at: datetime


class PreviewOut(BaseModel):
    due: list[PreviewFile]
    upcoming: list[PreviewFile]
    due_bytes: int
    upcoming_bytes: int


class TrashList(BaseModel):
    items: list[TrashOut]
    trash_bytes: int
    reclaimed_bytes: int


class EmptyTrashIn(BaseModel):
    confirm: bool = False


class EmptyTrashOut(BaseModel):
    purged: int
    bytes: int
    size_label: str


class MigrationBannerOut(BaseModel):
    message: str | None = None


def _out(row: TrashEntry) -> TrashOut:
    return TrashOut(
        id=row.id,
        original_path=row.original_path,
        label=row.label,
        reason=row.reason,
        size=row.size,
        deleted_at=row.deleted_at,
        purge_after=row.purge_after,
        media_id=row.media_id,
        season=row.season,
        restored=row.restored_at is not None,
        purged=row.purged_at is not None,
    )


def _guard(exc: CleanupError) -> Problem:
    missing = "introuvable" in exc.message.lower()
    status = 404 if missing else 409
    title = "Introuvable" if missing else "Conflit"
    return Problem(status, title, exc.message)


@router.get("/preview", response_model=PreviewOut)
def cleanup_preview(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> PreviewOut:
    del _principal
    due, upcoming = preview(db, request.app.state.settings)
    return _preview_out(due, upcoming)


@router.get("/migration-banner", response_model=MigrationBannerOut)
def cleanup_migration_banner(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> MigrationBannerOut:
    del _principal
    row = db.get(Setting, MIGRATION_NOTICE_KEY)
    text = (row.value or "").strip() if row is not None else ""
    return MigrationBannerOut(message=text or None)


@router.post("/migration-banner/dismiss", response_model=MigrationBannerOut)
def dismiss_cleanup_migration_banner(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> MigrationBannerOut:
    del _principal
    row = db.get(Setting, MIGRATION_NOTICE_KEY)
    if row is not None:
        row.value = ""
    db.commit()
    return MigrationBannerOut(message=None)


@router.post("/files", response_model=DeleteResult)
def remove_files(
    body: DeleteFilesIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> DeleteResult:
    del _principal
    media = db.get(Media, body.media_id)
    if media is None:
        raise Problem(404, "Introuvable", "Média introuvable.")
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    needs_confirm = manual_trash_requires_confirm(
        db,
        media,
        items,
        scope=body.scope,
        season=body.season,
        item_id=body.item_id,
    )
    if needs_confirm and not body.confirm:
        raise Problem(
            422,
            "Requête invalide",
            "Cet élément est marqué « Garder ». Confirmez la mise à la corbeille.",
        )
    try:
        outcome = delete_files(
            db,
            request.app.state.settings,
            media_id=body.media_id,
            scope=body.scope,
            season=body.season,
            item_id=body.item_id,
        )
    except CleanupError as exc:
        db.rollback()
        raise _guard(exc) from exc
    return DeleteResult(
        moved=len(outcome.entries),
        bytes=sum(entry.size for entry in outcome.entries),
        entries=[_out(entry) for entry in outcome.entries],
        missing=[
            MissingFileOut(path=path, message="Fichier déjà absent du disque") for path in outcome.missing
        ],
    )


@trash_router.get("", response_model=TrashList)
def list_trash(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> TrashList:
    del _principal
    rows = list(db.scalars(select(TrashEntry).order_by(TrashEntry.id.desc())).all())
    held, purged = reclaimed_bytes(db)
    active = [row for row in rows if row.restored_at is None and row.purged_at is None]
    return TrashList(
        items=[_out(row) for row in active],
        trash_bytes=held,
        reclaimed_bytes=purged,
    )


@trash_router.post("/empty", response_model=EmptyTrashOut)
def empty_trash(
    body: EmptyTrashIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> EmptyTrashOut:
    del _principal
    if not body.confirm:
        raise Problem(422, "Requête invalide", "Confirmez le vidage de la corbeille.")
    try:
        purged, freed = purge_all(db, request.app.state.settings)
    except CleanupError as exc:
        db.rollback()
        raise _guard(exc) from exc
    return EmptyTrashOut(purged=purged, bytes=freed, size_label=format_bytes(freed))


@trash_router.post("/{entry_id}/restore", response_model=TrashOut)
def restore_trash(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> TrashOut:
    del _principal
    try:
        entry = restore(db, request.app.state.settings, entry_id)
    except CleanupError as exc:
        db.rollback()
        raise _guard(exc) from exc
    return _out(entry)


@trash_router.delete("/{entry_id}", response_model=TrashOut)
def purge_trash(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> TrashOut:
    del _principal
    try:
        entry = purge_one(db, request.app.state.settings, entry_id)
    except CleanupError as exc:
        db.rollback()
        raise _guard(exc) from exc
    return _out(entry)


def _preview_out(due, upcoming) -> PreviewOut:
    def files(groups) -> list[PreviewFile]:
        rows: list[PreviewFile] = []
        for group in groups:
            for planned in group.plan.files:
                rows.append(
                    PreviewFile(
                        media_id=group.plan.media.id,
                        title=group.plan.media.title,
                        season=planned.item.season,
                        episode=planned.item.episode,
                        path=str(planned.path),
                        bytes=planned.size,
                        delete_at=group.plan.eligible_at,
                    )
                )
        return rows

    due_rows = files(due)
    upcoming_rows = files(upcoming)
    return PreviewOut(
        due=due_rows,
        upcoming=upcoming_rows,
        due_bytes=sum(row.bytes for row in due_rows),
        upcoming_bytes=sum(row.bytes for row in upcoming_rows),
    )
