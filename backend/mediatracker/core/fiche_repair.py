"""One-click fiche repair: disk check, reanalyze, source reread, Jellyfin, status."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.file_status import FileStatusCounts, FileStatusIndex, aggregate_file_status
from mediatracker.core.reanalyze import _run as reanalyze_run
from mediatracker.core.source_poll import close_source, refresh_library_claims, reread_source_for_repair
from mediatracker.db.models import Media, MediaItem, Setting
from mediatracker.events import record_event

_registry_guard = threading.Lock()
_active: dict[int, threading.Lock] = {}


class FicheRepairBusy(Exception):
    """Another repair is already running for this fiche."""


@dataclass
class RepairStepResult:
    name: str
    ok: bool
    error: str | None = None
    detail: str = ""


@dataclass
class FicheRepairReport:
    at: datetime
    summary: str
    detail: str
    steps: list[RepairStepResult] = field(default_factory=list)
    files_found: int = 0
    files_missing: int = 0
    links_available: int = 0
    corrected: list[str] = field(default_factory=list)
    file_status: str = "absent"
    file_status_counts: FileStatusCounts = field(default_factory=FileStatusCounts)
    detached: int = 0
    removed: int = 0
    restored: int = 0
    attached: int = 0


def _lock_for(media_id: int) -> threading.Lock:
    with _registry_guard:
        lock = _active.get(media_id)
        if lock is None:
            lock = threading.Lock()
            _active[media_id] = lock
        return lock


def repair_fiche(
    db: Session,
    settings: Settings,
    media: Media,
    *,
    now: datetime | None = None,
    source: object | None = None,
    jellyfin_client: object | None = None,
    emit_event: bool = True,
) -> FicheRepairReport:
    """Run the repair pipeline for one fiche. One concurrent run per media id."""
    lock = _lock_for(media.id)
    if not lock.acquire(blocking=False):
        raise FicheRepairBusy()
    try:
        return _run(
            db,
            settings,
            media,
            now=now,
            source=source,
            jellyfin_client=jellyfin_client,
            emit_event=emit_event,
        )
    finally:
        lock.release()


def run_repair_pipeline(
    db: Session,
    settings: Settings,
    media: Media,
    *,
    now: datetime | None = None,
    source: object | None = None,
    jellyfin_client: object | None = None,
    emit_event: bool = True,
) -> FicheRepairReport:
    """Repair one fiche without the per-media concurrency lock (library verify-all)."""
    return _run(
        db,
        settings,
        media,
        now=now,
        source=source,
        jellyfin_client=jellyfin_client,
        emit_event=emit_event,
    )


def _run(
    db: Session,
    settings: Settings,
    media: Media,
    *,
    now: datetime | None,
    source: object | None,
    jellyfin_client: object | None,
    emit_event: bool,
) -> FicheRepairReport:
    moment = now or datetime.now(timezone.utc)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    corrected: list[str] = []
    steps: list[RepairStepResult] = []
    links_available = 0
    after_counts = FileStatusCounts()
    detached = removed = restored = attached = 0

    # 1. Cross-series cleanup (library.repair) for this fiche only
    try:
        if _cross_series_applies(db, settings, media, items):
            from mediatracker.library.repair import repair_media

            cross = repair_media(db, settings, media)
            detached, removed, restored, attached = (
                cross.detached,
                cross.removed,
                cross.restored,
                cross.attached,
            )
            step_detail = _cross_series_detail(cross)
            if cross.detached or cross.removed or cross.restored or cross.attached:
                if cross.detached:
                    corrected.append(f"{cross.detached} fichier(s) détaché(s)")
                if cross.removed:
                    corrected.append(f"{cross.removed} épisode(s) étranger(s) retiré(s)")
                if cross.attached:
                    corrected.append(f"{cross.attached} fichier(s) rattaché(s) au dossier")
            steps.append(RepairStepResult(name="cross_series", ok=True, detail=step_detail))
            items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        else:
            steps.append(
                RepairStepResult(
                    name="cross_series",
                    ok=True,
                    detail="Aucune série croisée à corriger sur cette fiche.",
                )
            )
    except Exception as exc:  # noqa: BLE001
        steps.append(RepairStepResult(name="cross_series", ok=False, error=str(exc)[:300]))

    # 2. Disk reverification
    try:
        before = FileStatusIndex.load(db, items)
        before_counts = before.counts_for(items)
        changed = refresh_library_claims(db, media, items, settings, moment)
        after = FileStatusIndex.load(db, items)
        after_counts = after.counts_for(items)
        detail = (
            f"{after_counts.present} présent(s), {after_counts.absent} absent(s), "
            f"{after_counts.trash} corbeille, {after_counts.downloading} en cours."
        )
        if changed:
            gained = after_counts.present - before_counts.present
            if gained > 0:
                corrected.append(f"{gained} fichier(s) retrouvé(s) sur disque")
        steps.append(RepairStepResult(name="disk", ok=True, detail=detail))
    except Exception as exc:  # noqa: BLE001 — each step is isolated
        steps.append(RepairStepResult(name="disk", ok=False, error=str(exc)[:300]))
        after_counts = FileStatusCounts()

    # 3. Targeted reanalyze (F11, F10 guard inside scanner)
    try:
        report = reanalyze_run(db, settings, media, season=None, now=moment)
        detail = f"{report.found} vu(x), {report.new} nouveau(x), {report.absent} marqué(s) absent(s)."
        if report.new:
            corrected.append(f"{report.new} fichier(s) rattaché(s) par réanalyse")
        steps.append(RepairStepResult(name="reanalyze", ok=True, detail=detail))
    except Exception as exc:  # noqa: BLE001
        steps.append(RepairStepResult(name="reanalyze", ok=False, error=str(exc)[:300]))

    # 4. Source page reread
    try:
        owned = source is None
        if owned:
            from mediatracker.hive.identity import hive_enabled_in_db
            from mediatracker.providers.sources.loading import enabled_remote_sources

            opened = enabled_remote_sources(db, settings)
            if not opened:
                if not hive_enabled_in_db(db):
                    raise ValueError("Aucune source distante configurée.")
                _provider_id, source = 0, object()
            else:
                _provider_id, source = opened[0]
        else:
            from mediatracker.core.source_binding import wawacity_provider_id

            _provider_id = wawacity_provider_id(db)
        try:
            reread = reread_source_for_repair(
                db,
                media,
                _provider_id,
                source,
                settings,
                now=moment,
            )
        finally:
            if owned:
                close_source(source)
        links_available = reread.links
        if reread.ok and reread.message and "mis en file" in reread.message:
            corrected.append("Liens source actualisés")
        steps.append(
            RepairStepResult(
                name="source",
                ok=reread.ok,
                error=reread.error,
                detail=reread.message or f"{links_available} lien(s) actif(s).",
            )
        )
    except Exception as exc:  # noqa: BLE001
        steps.append(RepairStepResult(name="source", ok=False, error=str(exc)[:300]))

    # 5. Jellyfin match for this fiche
    try:
        matched, jellyfin_detail, jellyfin_error = _jellyfin_step(
            db, settings, media, moment, client=jellyfin_client
        )
        if matched:
            corrected.append(f"{matched} épisode(s) rapproché(s) avec Jellyfin")
        steps.append(
            RepairStepResult(
                name="jellyfin",
                ok=jellyfin_error is None,
                error=jellyfin_error,
                detail=jellyfin_detail,
            )
        )
    except Exception as exc:  # noqa: BLE001
        steps.append(RepairStepResult(name="jellyfin", ok=False, error=str(exc)[:300]))

    # 6. Status recalculation (read-time index)
    try:
        db.refresh(media)
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        index = FileStatusIndex.load(db, items)
        counts = index.counts_for(items)
        status = aggregate_file_status(counts)
        detail = (
            f"Statut fiche : {status} "
            f"({counts.present} présent, {counts.absent} absent, {counts.trash} corbeille)."
        )
        steps.append(RepairStepResult(name="status", ok=True, detail=detail))
        files_found = counts.present
        files_missing = counts.absent
        file_status = status
        file_status_counts = counts
    except Exception as exc:  # noqa: BLE001
        steps.append(RepairStepResult(name="status", ok=False, error=str(exc)[:300]))
        files_found = after_counts.present
        files_missing = after_counts.absent
        index = FileStatusIndex.load(db, items)
        counts = index.counts_for(items)
        file_status = aggregate_file_status(counts)
        file_status_counts = counts

    summary = _build_summary(files_found, files_missing, links_available, corrected, steps)
    cross_detail = _cross_series_detail_from_counts(detached, removed)
    detail = f"{cross_detail} {summary}".strip()
    media.last_repair_at = moment
    media.last_repair_summary = summary
    from mediatracker.core.fiche_check import touch_fiche_checked

    touch_fiche_checked(media, moment)
    if emit_event:
        record_event(
            db,
            "media.repaired",
            media_id=media.id,
            when=moment,
            payload={
                "title": media.title,
                "summary": summary,
                "detail": detail,
                "files_found": files_found,
                "files_missing": files_missing,
                "links_available": links_available,
                "corrected": corrected,
                "detached": detached,
                "removed": removed,
                "restored": restored,
                "attached": attached,
                "steps": [
                    {"name": step.name, "ok": step.ok, "error": step.error, "detail": step.detail}
                    for step in steps
                ],
            },
        )
    return FicheRepairReport(
        at=moment,
        summary=summary,
        detail=detail,
        steps=steps,
        files_found=files_found,
        files_missing=files_missing,
        links_available=links_available,
        corrected=corrected,
        file_status=file_status,
        file_status_counts=file_status_counts,
        detached=detached,
        removed=removed,
        restored=restored,
        attached=attached,
    )


def _cross_series_detail(report: object) -> str:
    return _cross_series_detail_from_counts(report.detached, report.removed)


def _cross_series_detail_from_counts(detached: int, removed: int) -> str:
    return (
        f"{detached} fichier(s) détaché(s), {removed} épisode(s) retiré(s). "
        "Aucun fichier n'a été supprimé du disque."
    )


def _cross_series_applies(db: Session, settings: Settings, media: Media, items: list[MediaItem]) -> bool:
    """True when library.repair may change this fiche (foreign paths or series rows to reconcile)."""
    if not items:
        return False
    if media.type != "series":
        from mediatracker.library.identity import file_belongs_to_media
        from mediatracker.library.scanner import library_roots

        movies, _series = library_roots(db, settings)
        return any(
            item.file_path and not file_belongs_to_media(Path(item.file_path), movies, media) for item in items
        )
    from mediatracker.library.identity import file_belongs_to_media
    from mediatracker.library.scanner import library_roots

    _movies, series_root = library_roots(db, settings)
    if any(
        item.file_path and not file_belongs_to_media(Path(item.file_path), series_root, media) for item in items
    ):
        return True
    if media.tmdb_id is not None:
        return True
    return False


def _build_summary(
    files_found: int,
    files_missing: int,
    links_available: int,
    corrected: list[str],
    steps: list[RepairStepResult],
) -> str:
    parts = [
        f"{files_found} fichier(s) présent(s)",
        f"{files_missing} absent(s)",
        f"{links_available} lien(s) disponible(s)",
    ]
    if corrected:
        parts.append("Corrigé : " + "; ".join(corrected))
    errors = [step for step in steps if not step.ok and step.error]
    if errors:
        parts.append(
            "Erreurs : "
            + "; ".join(f"{step.name} ({step.error})" for step in errors if step.error)
        )
    return ". ".join(parts) + "."


def _jellyfin_step(
    db: Session,
    settings: Settings,
    media: Media,
    moment: datetime,
    *,
    client: object | None,
) -> tuple[int, str, str | None]:
    from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, from_api_item
    from mediatracker.integrations.jellyfin_sync import (
        LAST_POLL_AT,
        _touch,
        apply_watches,
        diagnose_media,
        jellyfin_settings,
        resolve_configured_user,
    )

    url, api_key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    if not url or not api_key:
        return 0, "Jellyfin non configuré.", None
    owns = client is None
    if client is None:
        client = JellyfinClient(url, api_key, user_id or None)
    try:
        resolved = resolve_configured_user(db, client, user_id, moment)
        raw_items = client.user_items(resolved)
        series_index = client.series_index(resolved) if hasattr(client, "series_index") else {}
    except JellyfinError as exc:
        return 0, "", exc.message
    finally:
        if owns:
            client.close()
    watches = [item for item in (from_api_item(row, series_index) for row in raw_items) if item is not None]
    apply_watches(
        db,
        watches,
        prefix or None,
        moment,
        user_filter=resolved,
        path_map=path_map,
        series_index=series_index,
    )
    _touch(db, LAST_POLL_AT, moment)
    synced = db.get(Setting, LAST_POLL_AT)
    report = diagnose_media(db, media, watches, synced.value if synced is not None else None, series_index)
    matched = len(report.get("episodes") or [])
    unmatched = len(report.get("unmatched") or [])
    detail = report.get("summary") or f"{matched} épisode(s) rapproché(s), {unmatched} en attente."
    return matched, detail, None
