"""CSV and Jellyfin-DB import of watch history (dry-run then confirm)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.integrations.jellyfin_db_export import (
    JellyfinExportError,
    correction_csv_rows,
    export_history_for_user_id_with_stats,
    list_users_from_session,
    rows_to_csv_text,
    rows_to_reimport_csv_text,
)
from mediatracker.integrations.jellyfin_db_sessions import (
    create_session,
    destroy_session,
    load_session,
    mark_backup_source,
    stream_backup_zip_upload,
    stream_sqlite_upload,
)
from mediatracker.integrations.jellyfin_efcore_data import BACKUP_ZIP_NAME
from mediatracker.integrations.jellyfin_history_import import (
    apply_import,
    history_lines_from_watch_rows,
    list_follow_candidates,
    parse_csv_text,
    preview_import,
    preview_to_dict,
)
from mediatracker.integrations.jellyfin_history_import import (
    refollow_imported as apply_refollow,
)
from mediatracker.integrations.jellyfin_history_jobs import (
    get_job,
    start_csv_apply_job,
    start_csv_preview_job,
    start_db_apply_job,
    start_db_preview_job,
)

JELLYFIN_HISTORY_CSV_MAX_BYTES = 5_000_000

router = APIRouter(prefix="/library/jellyfin-history", tags=["library"])


class ImportPreviewOut(BaseModel):
    line_count: int
    matched_count: int
    create_count: int = Field(description="Lignes d'historique sans fiche existante (compatibilité).")
    create_media_count: int = Field(0, description="Fiches distinctes à créer (séries + films).")
    create_series_count: int = Field(0, description="Séries distinctes à créer.")
    create_film_count: int = Field(0, description="Films distincts à créer.")
    unmatched_count: int
    skipped_count: int
    identify_pending_count: int = 0
    identify_no_token_count: int = 0
    identify_no_results_count: int = 0
    export_userdata_count: int = 0
    export_activity_log_count: int = 0
    export_deleted_untitled_count: int = 0
    export_message: str | None = None
    export_sure_movies: int = 0
    export_sure_episodes: int = 0
    export_probable_count: int = 0
    export_non_importable_count: int = 0
    export_correction_hint: str | None = None
    matched: list[dict] = Field(default_factory=list)
    to_create: list[dict] = Field(default_factory=list)
    unmatched: list[dict] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)


class FollowCandidateOut(BaseModel):
    id: int
    title: str
    year: int | None
    type: str
    tmdb_id: int | None
    followed: bool


class FollowCandidatesOut(BaseModel):
    items: list[FollowCandidateOut]


class ImportApplyOut(ImportPreviewOut):
    applied: int
    created_media: int
    already_ok: int
    message: str
    follow_candidates: list[FollowCandidateOut] = Field(default_factory=list)


class RefollowIn(BaseModel):
    media_ids: list[int] = Field(default_factory=list)


class RefollowOut(BaseModel):
    updated: int
    message: str


class DbSessionOut(BaseModel):
    session_id: str


class JellyfinUserOut(BaseModel):
    id: str
    username: str


class DbUsersOut(BaseModel):
    users: list[JellyfinUserOut]


class DbUserIn(BaseModel):
    user_id: str = Field(min_length=1)
    include_probable: bool = Field(
        default=False,
        description="Inclure les lignes « probables » dans l'import (décoché par défaut).",
    )


class ImportJobStartedOut(BaseModel):
    job_id: str
    status: str = "running"


class ImportJobProgressOut(BaseModel):
    phase: str
    done: int = 0
    total: int = 0
    percent: int = 0
    message: str | None = None


class ImportJobStatusOut(BaseModel):
    job_id: str
    kind: str
    status: str
    progress: ImportJobProgressOut
    result: ImportPreviewOut | ImportApplyOut | None = None
    error: dict | None = None


async def _read_csv(upload: UploadFile) -> str:
    if not upload.filename or not upload.filename.lower().endswith(".csv"):
        raise Problem(422, "Requête invalide", "Envoyez un fichier .csv.")
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = await upload.read(64 * 1024)
        if not piece:
            break
        total += len(piece)
        if total > JELLYFIN_HISTORY_CSV_MAX_BYTES:
            raise Problem(413, "Fichier trop volumineux", "Le CSV dépasse la taille maximale (5 Mo).")
        chunks.append(piece)
    raw = b"".join(chunks)
    if not raw:
        raise Problem(422, "Requête invalide", "Fichier vide.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Problem(422, "Requête invalide", "Le CSV doit être encodé en UTF-8.") from exc


def _candidates_out(rows: list[dict]) -> list[FollowCandidateOut]:
    return [FollowCandidateOut(**row) for row in rows]


def _export_error(exc: JellyfinExportError) -> Problem:
    return Problem(422, "Base Jellyfin invalide", str(exc))


def _export_bundle_for_session(request: Request, session_id: str, user_id: str):
    settings = request.app.state.settings
    session = load_session(settings.config_dir, session_id)
    try:
        return export_history_for_user_id_with_stats(
            jellyfin_db=session.jellyfin_db,
            backup_zip=session.backup_zip,
            user_id=user_id,
        )
    except JellyfinExportError as exc:
        raise _export_error(exc) from exc


def _lines_for_session(request: Request, session_id: str, user_id: str, *, include_probable: bool = False):
    bundle = _export_bundle_for_session(request, session_id, user_id)
    return history_lines_from_watch_rows(bundle.rows, include_probable=include_probable), bundle.stats


def _job_status_out(raw: dict[str, Any]) -> ImportJobStatusOut:
    progress = raw.get("progress") or {}
    result = raw.get("result")
    parsed_result = None
    if isinstance(result, dict) and raw.get("status") == "complete":
        if "applied" in result:
            if "follow_candidates" in result and result["follow_candidates"]:
                result = {
                    **result,
                    "follow_candidates": _candidates_out(result["follow_candidates"]),
                }
            parsed_result = ImportApplyOut(**result)
        else:
            parsed_result = ImportPreviewOut(**result)
    return ImportJobStatusOut(
        job_id=str(raw["job_id"]),
        kind=str(raw.get("kind") or ""),
        status=str(raw.get("status") or "running"),
        progress=ImportJobProgressOut(
            phase=str(progress.get("phase") or "running"),
            done=int(progress.get("done") or 0),
            total=int(progress.get("total") or 0),
            percent=int(progress.get("percent") or 0),
            message=progress.get("message"),
        ),
        result=parsed_result,
        error=raw.get("error") if isinstance(raw.get("error"), dict) else None,
    )


def _preview_dict(preview, stats) -> dict:
    body = preview_to_dict(preview)
    correction_hint = None
    if (stats.probable_count or stats.non_importable_count) > 0:
        correction_hint = (
            "Téléchargez le CSV des probables et non importables, complétez-le puis réimportez-le "
            "via l'onglet Fichier CSV."
        )
    body.update(
        {
            "export_userdata_count": stats.userdata_count,
            "export_activity_log_count": stats.activity_log_count,
            "export_deleted_untitled_count": stats.deleted_untitled_count,
            "export_message": stats.empty_message,
            "export_sure_movies": stats.sure_movies,
            "export_sure_episodes": stats.sure_episodes,
            "export_probable_count": stats.probable_count,
            "export_non_importable_count": stats.non_importable_count,
            "export_correction_hint": correction_hint,
        }
    )
    return body


@router.get("/jobs/{job_id}", response_model=ImportJobStatusOut)
def jellyfin_import_job_status(
    job_id: str,
    _principal=Depends(require_scope("read")),
) -> ImportJobStatusOut:
    del _principal
    row = get_job(job_id)
    if row is None:
        raise Problem(404, "Tâche introuvable", "Cette prévisualisation ou import n'est plus disponible.")
    return _job_status_out(row)


@router.post("/import", response_model=ImportPreviewOut | ImportApplyOut | ImportJobStartedOut)
async def import_jellyfin_history(
    request: Request,
    upload: UploadFile = File(...),
    dry_run: bool = Query(default=True, description="Prévisualisation sans modification"),
    sync: bool = Query(default=False, description="Exécution synchrone (tests ou petits fichiers)"),
    db=Depends(get_db),
    _principal=Depends(require_scope("write")),
):
    del _principal, db
    text = await _read_csv(upload)
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    if sync:
        lines = parse_csv_text(text)
        session = factory()
        try:
            if dry_run:
                preview = preview_import(
                    session,
                    lines,
                    settings=settings,
                    secret_key=settings.secret_key,
                )
                return ImportPreviewOut(**preview_to_dict(preview))
            result = apply_import(session, settings, settings.secret_key, lines)
            session.commit()
        finally:
            session.close()
        body = preview_to_dict(result.preview)
        body.update(
            {
                "applied": result.applied,
                "created_media": result.created_media,
                "already_ok": result.already_ok,
                "follow_candidates": _candidates_out(result.follow_candidates),
                "message": (
                    f"{result.applied} marquage(s) appliqué(s), {result.created_media} fiche(s) créée(s), "
                    f"{len(result.preview.unmatched)} ligne(s) non identifiée(s)."
                ),
            }
        )
        return ImportApplyOut(**body)
    if dry_run:
        job_id = start_csv_preview_job(factory, settings, csv_text=text)
    else:
        job_id = start_csv_apply_job(factory, settings, csv_text=text)
    return JSONResponse(ImportJobStartedOut(job_id=job_id).model_dump(), status_code=202)


@router.post("/db/session", response_model=DbSessionOut)
async def create_jellyfin_db_session(
    request: Request,
    jellyfin_db: UploadFile | None = File(default=None, description="Copie jellyfin.db"),
    backup_zip: UploadFile | None = File(default=None, description="Archive de sauvegarde Jellyfin 10.11"),
    _principal=Depends(require_scope("write")),
) -> DbSessionOut:
    del _principal
    settings = request.app.state.settings
    max_bytes = settings.jellyfin_history_upload_max_bytes
    if jellyfin_db is None and backup_zip is None:
        raise Problem(422, "Requête invalide", "Envoyez jellyfin.db ou une archive de sauvegarde (.zip).")
    session = create_session(settings.config_dir)
    try:
        if backup_zip is not None and backup_zip.filename:
            dest = session.root / BACKUP_ZIP_NAME
            await stream_backup_zip_upload(backup_zip, dest, max_bytes=max_bytes)
            mark_backup_source(settings.config_dir, session.session_id)
        elif jellyfin_db is not None and jellyfin_db.filename:
            await stream_sqlite_upload(jellyfin_db, session.jellyfin_db, max_bytes=max_bytes)
        else:
            raise Problem(422, "Requête invalide", "Fichier Jellyfin manquant.")
    except Exception:
        destroy_session(settings.config_dir, session.session_id)
        raise
    return DbSessionOut(session_id=session.session_id)


@router.get("/db/session/{session_id}/users", response_model=DbUsersOut)
def list_jellyfin_db_users(
    request: Request,
    session_id: str,
    _principal=Depends(require_scope("read")),
) -> DbUsersOut:
    del _principal
    settings = request.app.state.settings
    session = load_session(settings.config_dir, session_id)
    try:
        users = list_users_from_session(
            jellyfin_db=session.jellyfin_db,
            backup_zip=session.backup_zip,
        )
    except JellyfinExportError as exc:
        raise _export_error(exc) from exc
    return DbUsersOut(users=[JellyfinUserOut(id=u.id, username=u.username) for u in users])


@router.post("/db/session/{session_id}/preview", response_model=ImportPreviewOut | ImportJobStartedOut)
def preview_jellyfin_db_import(
    request: Request,
    session_id: str,
    body: DbUserIn,
    sync: bool = Query(default=False, description="Exécution synchrone (tests ou petits fichiers)"),
    db=Depends(get_db),
    _principal=Depends(require_scope("write")),
):
    del _principal
    if sync:
        lines, stats = _lines_for_session(request, session_id, body.user_id, include_probable=body.include_probable)
        settings = request.app.state.settings
        preview = preview_import(
            db,
            lines,
            settings=settings,
            secret_key=settings.secret_key,
        )
        return ImportPreviewOut(**_preview_dict(preview, stats))
    settings = request.app.state.settings
    factory = request.app.state.session_factory

    def export_bundle():
        return _export_bundle_for_session(request, session_id, body.user_id)

    job_id = start_db_preview_job(
        factory,
        settings,
        session_id=session_id,
        user_id=body.user_id,
        include_probable=body.include_probable,
        export_fn=export_bundle,
    )
    return JSONResponse(ImportJobStartedOut(job_id=job_id).model_dump(), status_code=202)


@router.post("/db/session/{session_id}/export-corrections.csv")
def download_jellyfin_db_corrections_csv(
    request: Request,
    session_id: str,
    body: DbUserIn,
    db=Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> Response:
    del _principal
    settings = request.app.state.settings
    bundle = _export_bundle_for_session(request, session_id, body.user_id)
    rows = correction_csv_rows(bundle, db=db, settings=settings, secret_key=settings.secret_key)
    csv_text = rows_to_reimport_csv_text(rows)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="historique-jellyfin-a-corriger.csv"'},
    )


@router.post("/db/session/{session_id}/export.csv")
def download_jellyfin_db_csv(
    request: Request,
    session_id: str,
    body: DbUserIn,
    _principal=Depends(require_scope("read")),
) -> Response:
    del _principal
    bundle = _export_bundle_for_session(request, session_id, body.user_id)
    csv_text = rows_to_csv_text(csv_rows=bundle.csv_rows)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="historique-jellyfin.csv"'},
    )


@router.post("/db/session/{session_id}/import", response_model=ImportPreviewOut | ImportApplyOut | ImportJobStartedOut)
def import_jellyfin_db_session(
    request: Request,
    session_id: str,
    body: DbUserIn,
    dry_run: bool = Query(default=True, description="Prévisualisation sans modification"),
    sync: bool = Query(default=False, description="Exécution synchrone (tests ou petits fichiers)"),
    db=Depends(get_db),
    _principal=Depends(require_scope("write")),
):
    settings = request.app.state.settings
    if sync:
        lines, stats = _lines_for_session(request, session_id, body.user_id, include_probable=body.include_probable)
        if dry_run:
            preview = preview_import(
                db,
                lines,
                settings=settings,
                secret_key=settings.secret_key,
            )
            return ImportPreviewOut(**_preview_dict(preview, stats))
        try:
            result = apply_import(db, settings, settings.secret_key, lines)
            db.commit()
        except Exception:
            destroy_session(settings.config_dir, session_id)
            raise
        destroy_session(settings.config_dir, session_id)
        body_out = _preview_dict(result.preview, stats)
        body_out.update(
            {
                "applied": result.applied,
                "created_media": result.created_media,
                "already_ok": result.already_ok,
                "follow_candidates": _candidates_out(result.follow_candidates),
                "message": (
                    f"{result.applied} marquage(s) appliqué(s), {result.created_media} fiche(s) créée(s), "
                    f"{len(result.preview.unmatched)} ligne(s) non identifiée(s)."
                ),
            }
        )
        return ImportApplyOut(**body_out)
    factory = request.app.state.session_factory

    def export_bundle():
        return _export_bundle_for_session(request, session_id, body.user_id)

    def destroy():
        destroy_session(settings.config_dir, session_id)

    if dry_run:
        job_id = start_db_preview_job(
            factory,
            settings,
            session_id=session_id,
            user_id=body.user_id,
            include_probable=body.include_probable,
            export_fn=export_bundle,
        )
    else:
        job_id = start_db_apply_job(
            factory,
            settings,
            session_id=session_id,
            user_id=body.user_id,
            include_probable=body.include_probable,
            export_fn=export_bundle,
            destroy_session_fn=destroy,
        )
    return JSONResponse(ImportJobStartedOut(job_id=job_id).model_dump(), status_code=202)


@router.delete("/db/session/{session_id}", status_code=204)
def abandon_jellyfin_db_session(
    request: Request,
    session_id: str,
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    destroy_session(request.app.state.settings.config_dir, session_id)


@router.get("/follow-candidates", response_model=FollowCandidatesOut)
def follow_candidates(
    db=Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> FollowCandidatesOut:
    del _principal
    return FollowCandidatesOut(items=_candidates_out(list_follow_candidates(db)))


@router.get("/unfollowed", response_model=FollowCandidatesOut, deprecated=True)
def list_imported_unfollowed(
    db=Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> FollowCandidatesOut:
    """Compatibilité : titres du dernier import encore sans suivi."""
    del _principal
    rows = [row for row in list_follow_candidates(db) if not row["followed"]]
    return FollowCandidatesOut(items=_candidates_out(rows))


@router.post("/refollow", response_model=RefollowOut)
def refollow_imported_media(
    body: RefollowIn,
    request: Request,
    db=Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> RefollowOut:
    del _principal
    updated = apply_refollow(db, body.media_ids)
    if updated:
        from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker

        wake_worker(db, request.app.state.settings, REASON_API_MEDIA, commit=False)
    db.commit()
    if not body.media_ids:
        message = "Aucune fiche sélectionnée."
    elif updated:
        message = f"{updated} fiche(s) suivies."
    else:
        message = "Aucune fiche activée (déjà suivies ou hors dernier import)."
    return RefollowOut(updated=updated, message=message)
