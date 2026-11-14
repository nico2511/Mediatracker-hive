"""Background preview/apply for Jellyfin history import (DB session and CSV)."""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from mediatracker.events import record_event
from mediatracker.integrations.jellyfin_db_export import JellyfinExportError
from mediatracker.integrations.jellyfin_history_import import (
    PARTIAL_IMPORT_HINT,
    apply_import,
    history_lines_from_watch_rows,
    parse_csv_text,
    preview_import,
    preview_to_dict,
)
from mediatracker.security.logging import redact

logger = logging.getLogger(__name__)

SESSION_EXPIRED_DETAIL = "Session d'import expirée, renvoyez le fichier."

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def reset_jobs() -> None:
    """Drop in-memory job state (tests)."""
    with _lock:
        _jobs.clear()


def _new_job_id() -> str:
    return secrets.token_urlsafe(18)


def _set_job(job_id: str, **fields: Any) -> None:
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        row.update(fields)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()


def _done_progress(total: int) -> dict[str, Any]:
    return {"phase": "done", "done": total, "total": total, "percent": 100}


def _progress(job_id: str, *, phase: str, done: int, total: int, message: str | None = None) -> None:
    percent = 0
    if total > 0:
        percent = min(100, int(done * 100 / total))
    elif phase == "export":
        percent = min(40, done)
    payload: dict[str, Any] = {
        "phase": phase,
        "done": done,
        "total": total,
        "percent": percent,
    }
    if message:
        payload["message"] = message
    _set_job(job_id, progress=payload)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        row = _jobs.get(job_id)
        return dict(row) if row else None


def start_db_preview_job(
    factory,
    settings,
    *,
    session_id: str,
    user_id: str,
    include_probable: bool,
    export_fn: Callable[[], tuple[Any, Any]],
) -> str:
    job_id = _new_job_id()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "kind": "preview_db",
            "status": "running",
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "progress": {"phase": "export", "done": 0, "total": 0, "percent": 0},
            "result": None,
            "error": None,
        }

    def run() -> None:
        try:
            _progress(job_id, phase="export", done=5, total=100, message="Lecture de la base Jellyfin…")
            bundle = export_fn()
            stats = bundle.stats
            _progress(job_id, phase="export", done=100, total=100, message="Export terminé.")
            lines = history_lines_from_watch_rows(bundle.rows, include_probable=include_probable)
            total_lines = len(lines)

            def on_line(done: int, total: int) -> None:
                _progress(
                    job_id,
                    phase="preview",
                    done=done,
                    total=total,
                    message=f"Analyse des lignes ({done}/{total})…",
                )

            db = factory()
            try:
                preview = preview_import(db, lines, on_progress=on_line)
                body = preview_to_dict(preview)
                body.update(_stats_dict(stats))
                _set_job(job_id, status="complete", result=body, progress=_done_progress(total_lines))
            finally:
                db.close()
        except JellyfinExportError as exc:
            _fail_job(factory, job_id, exc, title="Base Jellyfin invalide")
        except Exception as exc:
            _fail_job(factory, job_id, exc, title="Prévisualisation impossible")

    threading.Thread(target=run, name=f"jf-import-preview-{job_id[:8]}", daemon=True).start()
    return job_id


def start_db_apply_job(
    factory,
    settings,
    *,
    session_id: str,
    user_id: str,
    include_probable: bool,
    export_fn: Callable[[], tuple[Any, Any]],
    destroy_session_fn: Callable[[], None],
) -> str:
    job_id = _new_job_id()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "kind": "import_db",
            "status": "running",
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "progress": {"phase": "export", "done": 0, "total": 0, "percent": 0},
            "result": None,
            "error": None,
        }

    def run() -> None:
        commits = 0
        try:
            _progress(job_id, phase="export", done=10, total=100, message="Lecture de la base Jellyfin…")
            bundle = export_fn()
            stats = bundle.stats
            lines = history_lines_from_watch_rows(bundle.rows, include_probable=include_probable)
            total_lines = len(lines)

            def on_line(done: int, total: int) -> None:
                _progress(
                    job_id,
                    phase="import",
                    done=done,
                    total=total,
                    message=f"Import ({done}/{total})…",
                )

            db = factory()
            commits = 0
            try:
                result = apply_import(
                    db,
                    settings,
                    settings.secret_key,
                    lines,
                    on_preview_progress=on_line,
                )
                db.commit()
                destroy_session_fn()
                body = preview_to_dict(result.preview)
                body.update(_stats_dict(stats))
                body.update(
                    {
                        "applied": result.applied,
                        "created_media": result.created_media,
                        "already_ok": result.already_ok,
                        "follow_candidates": result.follow_candidates,
                        "message": (
                            f"{result.applied} marquage(s) appliqué(s), {result.created_media} fiche(s) créée(s), "
                            f"{len(result.preview.unmatched)} ligne(s) non identifiée(s)."
                        ),
                    }
                )
                _set_job(job_id, status="complete", result=body, progress=_done_progress(total_lines))
            except Exception:
                commits = int(db.info.get("import_commits") or 0)
                raise
            finally:
                db.close()
        except Exception as exc:
            try:
                destroy_session_fn()
            except Exception:
                logger.exception("Nettoyage session import après échec.")
            _fail_job(factory, job_id, exc, title="Import impossible", import_commits=commits)

    threading.Thread(target=run, name=f"jf-import-apply-{job_id[:8]}", daemon=True).start()
    return job_id


def start_csv_preview_job(factory, settings, *, csv_text: str) -> str:
    job_id = _new_job_id()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "kind": "preview_csv",
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "progress": {"phase": "parse", "done": 0, "total": 0, "percent": 0},
            "result": None,
            "error": None,
        }

    def run() -> None:
        try:
            lines = parse_csv_text(csv_text)
            total = len(lines)

            def on_line(done: int, total_lines: int) -> None:
                _progress(
                    job_id,
                    phase="preview",
                    done=done,
                    total=total_lines,
                    message=f"Analyse ({done}/{total_lines})…",
                )

            db = factory()
            try:
                preview = preview_import(db, lines, on_progress=on_line)
                body = preview_to_dict(preview)
                _set_job(job_id, status="complete", result=body, progress=_done_progress(total))
            finally:
                db.close()
        except Exception as exc:
            _fail_job(factory, job_id, exc, title="Prévisualisation impossible")

    threading.Thread(target=run, name=f"jf-csv-preview-{job_id[:8]}", daemon=True).start()
    return job_id


def start_csv_apply_job(factory, settings, *, csv_text: str) -> str:
    job_id = _new_job_id()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "kind": "import_csv",
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "progress": {"phase": "parse", "done": 0, "total": 0, "percent": 0},
            "result": None,
            "error": None,
        }

    def run() -> None:
        try:
            lines = parse_csv_text(csv_text)

            def on_line(done: int, total_lines: int) -> None:
                _progress(
                    job_id,
                    phase="import",
                    done=done,
                    total=total_lines,
                    message=f"Import ({done}/{total_lines})…",
                )

            db = factory()
            commits = 0
            try:
                result = apply_import(
                    db,
                    settings,
                    settings.secret_key,
                    lines,
                    on_preview_progress=on_line,
                )
                db.commit()
                body = preview_to_dict(result.preview)
                body.update(
                    {
                        "applied": result.applied,
                        "created_media": result.created_media,
                        "already_ok": result.already_ok,
                        "follow_candidates": result.follow_candidates,
                        "message": (
                            f"{result.applied} marquage(s) appliqué(s), {result.created_media} fiche(s) créée(s), "
                            f"{len(result.preview.unmatched)} ligne(s) non identifiée(s)."
                        ),
                    }
                )
                _set_job(
                    job_id,
                    status="complete",
                    result=body,
                    progress=_done_progress(len(lines)),
                )
            except Exception:
                commits = int(db.info.get("import_commits") or 0)
                raise
            finally:
                db.close()
        except Exception as exc:
            _fail_job(factory, job_id, exc, title="Import impossible", import_commits=commits)

    threading.Thread(target=run, name=f"jf-csv-apply-{job_id[:8]}", daemon=True).start()
    return job_id


def _stats_dict(stats) -> dict[str, Any]:
    correction_hint = None
    if (stats.probable_count or stats.non_importable_count) > 0:
        correction_hint = (
            "Téléchargez le CSV des probables et non importables, complétez-le puis réimportez-le "
            "via l'onglet Fichier CSV."
        )
    return {
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


def _fail_job(factory, job_id: str, exc: Exception, *, title: str, import_commits: int = 0) -> None:
    message = redact(str(exc) or type(exc).__name__)
    row = get_job(job_id)
    kind = row.get("kind") if row else None
    detail = message
    if kind in {"import_db", "import_csv"} and import_commits > 0:
        detail = f"{message} {PARTIAL_IMPORT_HINT}"
    logger.exception("Tâche import Jellyfin %s en échec : %s", job_id, message)
    try:
        db = factory()
        try:
            record_event(
                db,
                "jellyfin_history.import_failed",
                level="error",
                payload={
                    "job_id": job_id,
                    "exception_type": type(exc).__name__,
                    "message": message,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("Journalisation échec import Jellyfin impossible (job %s).", job_id)
    _set_job(
        job_id,
        status="error",
        error={"title": title, "detail": detail, "exception_type": type(exc).__name__},
        progress={"phase": "error", "done": 0, "total": 0, "percent": 0},
    )


def wait_job(job_id: str, *, timeout: float = 120.0, poll: float = 0.05) -> dict[str, Any]:
    """Block until job completes (tests, sync=1)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = get_job(job_id)
        if row is None:
            raise KeyError(job_id)
        if row["status"] in {"complete", "error"}:
            return row
        time.sleep(poll)
    raise TimeoutError(f"job {job_id} still running after {timeout}s")
