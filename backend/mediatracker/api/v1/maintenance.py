"""Administrative maintenance. Reset always writes a backup first."""

from datetime import datetime

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.maintenance.backup_import import BackupImportError, import_backup_upload
from mediatracker.maintenance.reset import ResetError, reset_database
from mediatracker.maintenance.restore import RestoreConflictError, RestoreError, restore_database
from mediatracker.maintenance.restore_catalog import (
    BackupCatalogError,
    count_fiches,
    list_backups,
    list_safety_backups,
    preview_backup,
    resolve_backup_file,
)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


class ResetIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=16)


class ResetOut(BaseModel):
    ok: bool
    backup: str
    detail: str


class BackupItemOut(BaseModel):
    name: str
    created_at: datetime
    size_bytes: int


class BackupListOut(BaseModel):
    items: list[BackupItemOut]


class BackupPreviewOut(BaseModel):
    name: str
    created_at: datetime
    app_version: str | None
    schema_revision: str | None
    fiches: int
    fiches_delta: int
    restorable: bool
    schema_relation: str
    imported: bool = False


class RestoreIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=16)


class RestoreOut(BaseModel):
    ok: bool
    backup: str
    safety_copy: str
    schema_revision: str | None
    detail: str


@router.get("/backups", response_model=BackupListOut)
def list_database_backups(
    request: Request,
    _principal=Depends(require_scope("write")),
) -> BackupListOut:
    """List SQLite backups available for restore."""
    del _principal
    rows = list_backups(request.app.state.settings)
    return BackupListOut(
        items=[
            BackupItemOut(name=row.name, created_at=row.created_at, size_bytes=row.size_bytes)
            for row in rows
        ],
    )


@router.get("/backups/safety", response_model=BackupListOut)
def list_safety_database_backups(
    request: Request,
    _principal=Depends(require_scope("write")),
) -> BackupListOut:
    """List SQLite safety copies under backups/safety (read-only listing)."""
    del _principal
    rows = list_safety_backups(request.app.state.settings)
    return BackupListOut(
        items=[
            BackupItemOut(name=row.name, created_at=row.created_at, size_bytes=row.size_bytes)
            for row in rows
        ],
    )


@router.get("/backups/safety/{name}/preview", response_model=BackupPreviewOut)
def preview_safety_database_backup(
    name: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> BackupPreviewOut:
    """Inspect one safety copy."""
    del _principal
    settings = request.app.state.settings
    current = count_fiches(settings.database_url)
    try:
        row = preview_backup(settings, name, current_fiches=current, safety=True)
    except BackupCatalogError as exc:
        raise Problem(404, "Sauvegarde", exc.message) from exc
    return BackupPreviewOut(
        name=row.name,
        created_at=row.created_at,
        app_version=row.app_version,
        schema_revision=row.schema_revision,
        fiches=row.fiches,
        fiches_delta=row.fiches_delta,
        restorable=row.restorable,
        schema_relation=row.schema_relation,
        imported=row.imported,
    )


@router.get("/backups/safety/{name}/download")
def download_safety_database_backup(
    name: str,
    request: Request,
    _principal=Depends(require_scope("admin")),
) -> FileResponse:
    del _principal
    settings = request.app.state.settings
    try:
        path = resolve_backup_file(settings, name, safety=True)
    except BackupCatalogError as exc:
        raise Problem(404, "Sauvegarde", exc.message) from exc
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/backups/safety/{name}/restore", response_model=RestoreOut)
def restore_safety_database_backup(
    name: str,
    body: RestoreIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RestoreOut:
    """Replace the live database from a safety copy."""
    del _principal
    if body.confirm != "RESTORE":
        raise Problem(422, "Requête invalide", "Écrivez RESTORE pour confirmer.")
    try:
        report = restore_database(
            request.app,
            request.app.state.settings,
            db,
            name,
            safety=True,
        )
    except BackupCatalogError as exc:
        raise Problem(404, "Sauvegarde", exc.message) from exc
    except RestoreConflictError as exc:
        raise Problem(409, "Restauration", exc.message) from exc
    except RestoreError as exc:
        raise Problem(422, "Restauration", exc.message) from exc
    return RestoreOut(
        ok=True,
        backup=report.backup,
        safety_copy=report.safety_copy,
        schema_revision=report.schema_revision,
        detail=report.detail,
    )


@router.get("/backups/{name}/preview", response_model=BackupPreviewOut)
def preview_database_backup(
    name: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> BackupPreviewOut:
    """Inspect one backup: version, schema revision, and fiche counts."""
    del _principal
    settings = request.app.state.settings
    current = count_fiches(settings.database_url)
    try:
        row = preview_backup(settings, name, current_fiches=current)
    except BackupCatalogError as exc:
        raise Problem(404, "Sauvegarde", exc.message) from exc
    return BackupPreviewOut(
        name=row.name,
        created_at=row.created_at,
        app_version=row.app_version,
        schema_revision=row.schema_revision,
        fiches=row.fiches,
        fiches_delta=row.fiches_delta,
        restorable=row.restorable,
        schema_relation=row.schema_relation,
        imported=row.imported,
    )


@router.get("/backups/{name}/download")
def download_database_backup(
    name: str,
    request: Request,
    _principal=Depends(require_scope("admin")),
) -> FileResponse:
    del _principal
    settings = request.app.state.settings
    try:
        path = resolve_backup_file(settings, name)
    except BackupCatalogError as exc:
        raise Problem(404, "Sauvegarde", exc.message) from exc
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/backups/import", response_model=BackupItemOut)
async def import_database_backup(
    request: Request,
    file: UploadFile = File(...),
    _principal=Depends(require_scope("admin")),
) -> BackupItemOut:
    del _principal
    if not file.filename or not file.filename.lower().endswith(".db"):
        raise Problem(422, "Requête invalide", "Choisissez un fichier .db.")
    try:
        row = await import_backup_upload(file, request.app.state.settings)
    except BackupImportError as exc:
        if "trop volumineux" in exc.message.lower():
            raise Problem(
                413,
                "Fichier trop volumineux",
                exc.message,
            ) from exc
        raise Problem(422, "Import refusé", exc.message) from exc
    return BackupItemOut(name=row.name, created_at=row.created_at, size_bytes=row.size_bytes)


@router.post("/backups/{name}/restore", response_model=RestoreOut)
def restore_database_backup(
    name: str,
    body: RestoreIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RestoreOut:
    """Replace the live database from a backup after an explicit confirmation."""
    del _principal
    if body.confirm != "RESTORE":
        raise Problem(422, "Requête invalide", "Écrivez RESTORE pour confirmer.")
    try:
        report = restore_database(request.app, request.app.state.settings, db, name)
    except BackupCatalogError as exc:
        raise Problem(404, "Sauvegarde", exc.message) from exc
    except RestoreConflictError as exc:
        raise Problem(409, "Restauration", exc.message) from exc
    except RestoreError as exc:
        raise Problem(422, "Restauration", exc.message) from exc
    return RestoreOut(
        ok=True,
        backup=report.backup,
        safety_copy=report.safety_copy,
        schema_revision=report.schema_revision,
        detail=report.detail,
    )


@router.post("/reset", response_model=ResetOut)
def reset_app_database(
    body: ResetIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> ResetOut:
    """Erase fiches, downloads and the library index. The backup stays on disk."""
    del _principal
    if body.confirm != "RESET":
        raise Problem(422, "Requête invalide", "Écrivez RESET pour confirmer.")
    try:
        dest = reset_database(db, request.app.state.settings)
    except ResetError as exc:
        raise Problem(422, "Réinitialisation", exc.message) from exc
    return ResetOut(
        ok=True,
        backup=dest.name,
        detail="Base réinitialisée. La sauvegarde est dans le dossier backups. Reconnectez-vous.",
    )
