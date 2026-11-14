"""Background verify-all: run the fiche repair pipeline on the whole library in batches."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.fiche_repair import FicheRepairReport, run_repair_pipeline
from mediatracker.core.library_guard import is_suspended
from mediatracker.core.metrics.worker_counters import (
    TASK_VERIFY_ALL,
    persist_worker_metrics,
    track_task,
)
from mediatracker.core.source_poll import close_source
from mediatracker.db.models import Media, Setting
from mediatracker.events import record_event

logger = logging.getLogger(__name__)

LAST_SUMMARY_KEY = "library.verify_all_last"
BATCH_SIZE = 20
BATCH_PAUSE_SECONDS = 0.5

_lock = threading.Lock()
_running = False
_progress: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "processed": 0,
    "total": 0,
    "batch": 0,
    "stop_reason": None,
}
_last_summary: dict[str, Any] | None = None


def reset_state() -> None:
    """Drop in-memory job state. Tests call this between cases."""
    global _running, _progress, _last_summary
    with _lock:
        _running = False
        _progress = {
            "running": False,
            "started_at": None,
            "processed": 0,
            "total": 0,
            "batch": 0,
            "stop_reason": None,
        }
        _last_summary = None


def snapshot(db: Session) -> dict[str, Any]:
    """Current job progress and last finished summary."""
    global _last_summary
    with _lock:
        running = _running
        progress = dict(_progress)
        last = _last_summary
    if last is None:
        last = _load_last_summary(db)
        if last is not None:
            with _lock:
                if _last_summary is None:
                    _last_summary = last
    progress["running"] = running
    return {"progress": progress, "last_summary": last}


def verify_all_running() -> bool:
    with _lock:
        return _running


def try_start(factory, settings: Settings) -> bool:
    """Start one verify-all pass. False when a pass is already running."""
    global _running
    with _lock:
        if _running:
            return False
        _running = True
        _progress.update(
            {
                "running": True,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "processed": 0,
                "total": 0,
                "batch": 0,
                "stop_reason": None,
            }
        )
    threading.Thread(
        target=_run,
        args=(factory, settings),
        name="library-verify-all",
        daemon=True,
    ).start()
    return True


def _run(factory, settings: Settings) -> None:
    global _running, _progress, _last_summary
    pause = _batch_pause
    batch_size = BATCH_SIZE
    started = datetime.now(timezone.utc)
    totals = _Totals()
    problems: list[dict[str, Any]] = []
    stop_reason: str | None = None
    with track_task(TASK_VERIFY_ALL):
        try:
            db = factory()
            try:
                media_ids = _media_ids(db)
                total = len(media_ids)
            finally:
                db.close()
            with _lock:
                _progress["total"] = total
            stop_reason = "complete"
            if total > 0:
                for batch_index, chunk in enumerate(_chunks(media_ids, batch_size), start=1):
                    db = factory()
                    try:
                        if is_suspended(db):
                            stop_reason = "library_suspended"
                            break
                    finally:
                        db.close()
                    _process_batch(factory, settings, chunk, started, totals, problems)
                    with _lock:
                        _progress["batch"] = batch_index
                    record_batch_progress(factory, batch_index, started)
                    if stop_reason != "library_suspended" and batch_index * batch_size < total:
                        pause()
                        db = factory()
                        try:
                            if is_suspended(db):
                                stop_reason = "library_suspended"
                        finally:
                            db.close()
                    if stop_reason == "library_suspended":
                        break
            finished = datetime.now(timezone.utc)
            summary = _build_summary(started, totals, problems, stop_reason, total, finished)
            db = factory()
            try:
                _save_last_summary(db, summary, finished)
                record_event(
                    db,
                    "library.verify_all.completed",
                    when=finished,
                    payload={
                        "title": "Bibliothèque",
                        "summary": summary["detail"],
                        "processed": summary["processed"],
                        "problems": len(problems),
                        "stopped_early": summary["stopped_early"],
                        "stop_reason": stop_reason,
                    },
                )
                db.commit()
            finally:
                db.close()
            with _lock:
                _last_summary = summary
            logger.info("Vérification bibliothèque terminée (%s fiches, %s problème(s)).", total, len(problems))
        except Exception:
            logger.exception("Vérification bibliothèque échouée.")
            with _lock:
                base = dict(_last_summary or {})
                base["error"] = "Vérification interrompue."
                _last_summary = base
        finally:
            db = factory()
            try:
                persist_worker_metrics(db, commit=True)
            finally:
                db.close()
            with _lock:
                _running = False
                _progress["running"] = False
                _progress["stop_reason"] = stop_reason


def record_batch_progress(factory, batch_index: int, started: datetime) -> None:
    with _lock:
        processed = _progress.get("processed", 0)
        total = _progress.get("total", 0)
    db = factory()
    try:
        record_event(
            db,
            "library.verify_all.progress",
            when=datetime.now(timezone.utc),
            dedupe_key=f"verify-all:{started.isoformat()}:batch:{batch_index}",
            payload={
                "title": "Bibliothèque",
                "batch": batch_index,
                "processed": processed,
                "total": total,
            },
        )
        db.commit()
    finally:
        db.close()


def _process_batch(
    factory,
    settings: Settings,
    media_ids: list[int],
    started: datetime,
    totals: _Totals,
    problems: list[dict[str, Any]],
) -> None:
    db = factory()
    try:
        from mediatracker.providers.sources.loading import enabled_remote_sources

        opened = enabled_remote_sources(db, settings)
        source = opened[0][1] if opened else None
    finally:
        db.close()
    try:
        for media_id in media_ids:
            db = factory()
            try:
                media = db.get(Media, media_id)
                if media is None:
                    continue
                report = run_repair_pipeline(
                    db,
                    settings,
                    media,
                    now=started,
                    source=source,
                    emit_event=False,
                )
                totals.add(report)
                if _has_problem(report):
                    problems.append(
                        {
                            "media_id": media.id,
                            "title": media.title,
                            "summary": report.summary,
                            "files_missing": report.files_missing,
                        }
                    )
                db.commit()
            finally:
                db.close()
            with _lock:
                _progress["processed"] = int(_progress.get("processed", 0)) + 1
    finally:
        if source is not None:
            close_source(source)


@dataclass
class _Totals:
    files_found: int = 0
    files_missing: int = 0
    links_available: int = 0
    step_errors: int = 0
    processed: int = 0

    def add(self, report: FicheRepairReport) -> None:
        self.files_found += report.files_found
        self.files_missing += report.files_missing
        self.links_available += report.links_available
        self.step_errors += sum(1 for step in report.steps if not step.ok)
        self.processed += 1


def _has_problem(report: FicheRepairReport) -> bool:
    if report.files_missing > 0:
        return True
    return any(not step.ok for step in report.steps)


def _build_summary(
    started: datetime,
    totals: _Totals,
    problems: list[dict[str, Any]],
    stop_reason: str | None,
    total: int,
    finished: datetime,
) -> dict[str, Any]:
    stopped_early = stop_reason == "library_suspended"
    detail = (
        f"{totals.processed} fiche(s) vérifiée(s) sur {total}. "
        f"{totals.files_found} fichier(s) présent(s), {totals.files_missing} absent(s). "
        f"{len(problems)} fiche(s) avec un problème."
    )
    if stopped_early:
        detail += " Arrêt : scan bibliothèque suspendu (F10)."
    return {
        "at": finished.isoformat(),
        "processed": totals.processed,
        "total": total,
        "files_found": totals.files_found,
        "files_missing": totals.files_missing,
        "links_available": totals.links_available,
        "step_errors": totals.step_errors,
        "problems": problems,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
        "detail": detail,
    }


def _media_ids(db: Session) -> list[int]:
    rows = db.scalars(select(Media.id).where(Media.followed.is_(True)).order_by(Media.id)).all()
    return list(rows)


def _chunks(ids: list[int], size: int) -> list[list[int]]:
    return [ids[index : index + size] for index in range(0, len(ids), size)]


def _load_last_summary(db: Session) -> dict[str, Any] | None:
    row = db.get(Setting, LAST_SUMMARY_KEY)
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _save_last_summary(db: Session, summary: dict[str, Any], now: datetime) -> None:
    payload = json.dumps(summary, ensure_ascii=False)
    row = db.get(Setting, LAST_SUMMARY_KEY)
    if row is None:
        db.add(Setting(key=LAST_SUMMARY_KEY, value=payload, is_secret=False, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now


def _batch_pause() -> None:
    time.sleep(BATCH_PAUSE_SECONDS)
