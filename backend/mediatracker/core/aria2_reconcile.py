"""One startup pass: align aria2's session with the download queue in the database."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.clients import _downloader
from mediatracker.core.pipeline.constants import ACTIVE
from mediatracker.db.models import Download
from mediatracker.providers.downloaders.aria2 import Aria2Error, Aria2QueueEntry

logger = logging.getLogger(__name__)

_DONE_ONCE = False
_LIST_FAIL_AT: datetime | None = None
_LIST_FAIL_LOGGED = False
_LIST_RETRY_COOLDOWN = timedelta(minutes=10)
_UNPAUSE_LAST: datetime | None = None
_UNPAUSE_COOLDOWN = timedelta(seconds=10)


def reset_startup_reconcile() -> None:
    """Tests only: allow another startup pass."""
    global _DONE_ONCE, _LIST_FAIL_AT, _LIST_FAIL_LOGGED, _UNPAUSE_LAST
    _DONE_ONCE = False
    _LIST_FAIL_AT = None
    _LIST_FAIL_LOGGED = False
    _UNPAUSE_LAST = None


def maybe_reconcile_aria2(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    downloader=None,
) -> None:
    """Wait until aria2 answers, then purge stale GIDs and re-queue orphaned rows once."""
    global _DONE_ONCE, _LIST_FAIL_AT, _LIST_FAIL_LOGGED
    if _DONE_ONCE:
        return
    moment = now or datetime.now(timezone.utc)
    if _LIST_FAIL_AT is not None and moment - _LIST_FAIL_AT < _LIST_RETRY_COOLDOWN:
        return
    _row, client = _downloader(db, settings, downloader)
    if client is None:
        _DONE_ONCE = True
        return
    health = client.health()
    if health.status != "ok":
        return
    try:
        entries = _list_entries(client)
    except Exception as exc:
        _LIST_FAIL_AT = moment
        if not _LIST_FAIL_LOGGED:
            logger.warning("Impossible de lire la file aria2 au démarrage : %s", exc)
            _LIST_FAIL_LOGGED = True
        return
    _LIST_FAIL_AT = None
    _LIST_FAIL_LOGGED = False
    living = {entry.gid for entry in entries}
    handles, names = _active_db_keys(db)
    purged = 0
    for entry in entries:
        if _should_purge(entry, handles, names):
            if _purge_entry(client, entry):
                purged += 1
            living.discard(entry.gid)
    requeued = _requeue_missing_handles(db, living, moment)
    unpaused = unpause_stale_paused_handles(db, settings, downloader=downloader, now=moment, force=True)
    from mediatracker.hive.download_pipeline import scrub_hive_grant_from_session_file, sync_hive_grants_for_active

    sync_hive_grants_for_active(db, settings, downloader, now=moment)
    _save_session(client)
    scrub_hive_grant_from_session_file(Path(settings.config_dir) / "aria2.session")
    db.flush()
    _DONE_ONCE = True
    if purged or requeued or unpaused:
        logger.info(
            "Passage aria2 au démarrage : %s entrée(s) purgée(s), %s job(s) remis en file, "
            "%s handle(s) aria2 repris.",
            purged,
            requeued,
            unpaused,
        )


def _list_entries(client) -> list[Aria2QueueEntry]:
    list_queue = getattr(client, "list_queue", None)
    if list_queue is not None:
        return list(list_queue())
    aria = getattr(client, "client", None)
    if aria is not None and hasattr(aria, "list_queue"):
        return list(aria.list_queue())
    return []


def unpause_stale_paused_handles(
    db: Session,
    settings: Settings,
    *,
    downloader=None,
    now: datetime | None = None,
    force: bool = False,
) -> int:
    """Resume aria2 handles left paused after a container restart (not user or disk pause)."""
    global _UNPAUSE_LAST
    from mediatracker.core.disk import _flag as disk_low_flag
    from mediatracker.core.queue_hold import HOLD_CODE, downloads_held

    if downloads_held(db) or disk_low_flag(db):
        return 0
    moment = now or datetime.now(timezone.utc)
    if not force and _UNPAUSE_LAST is not None and moment - _UNPAUSE_LAST < _UNPAUSE_COOLDOWN:
        return 0
    _row, client = _downloader(db, settings, downloader)
    if client is None:
        return 0
    resume = getattr(client, "resume", None)
    if resume is None:
        return 0
    try:
        entries = _list_entries(client)
    except Exception as exc:
        logger.info("Lecture file aria2 ignorée pour reprise : %s", exc)
        return 0
    handle_rows: dict[str, Download] = {}
    for row in db.scalars(select(Download).where(Download.state.in_(ACTIVE))).all():
        if row.handle:
            handle_rows[row.handle] = row
    count = 0
    for entry in entries:
        if entry.status != "paused":
            continue
        row = handle_rows.get(entry.gid)
        if row is None:
            continue
        if row.error_code in {HOLD_CODE, "DISK_LOW"}:
            continue
        try:
            resume(entry.gid)
        except Exception as exc:
            logger.info("Reprise aria2 ignorée pour %s : %s", entry.gid, exc)
            continue
        count += 1
    if count or force:
        from mediatracker.hive.download_pipeline import maybe_refresh_hive_grant, scrub_hive_grant_from_session_file

        for entry in entries:
            if entry.status != "paused":
                continue
            row = handle_rows.get(entry.gid)
            if row is None:
                continue
            maybe_refresh_hive_grant(db, settings, row, downloader, now=moment, force=True)
        _save_session(client)
        scrub_hive_grant_from_session_file(Path(settings.config_dir) / "aria2.session")
        _UNPAUSE_LAST = moment
    return count


def _active_db_keys(db: Session) -> tuple[set[str], set[str]]:
    handles: set[str] = set()
    names: set[str] = set()
    rows = db.scalars(select(Download).where(Download.state.in_(ACTIVE))).all()
    for row in rows:
        if row.handle:
            handles.add(row.handle)
        if row.filename:
            names.add(Path(row.filename).name)
        if row.state == "post_processing" and row.error and row.error.startswith("/"):
            names.add(Path(row.error).name)
    return handles, names


def _matches_db(entry: Aria2QueueEntry, handles: set[str], names: set[str]) -> bool:
    if entry.gid in handles:
        return True
    if entry.filename and entry.filename in names:
        return True
    return False


def _should_purge(entry: Aria2QueueEntry, handles: set[str], names: set[str]) -> bool:
    if entry.status in {"error", "removed"}:
        return True
    if _matches_db(entry, handles, names):
        return False
    return True


def _purge_entry(client, entry: Aria2QueueEntry) -> bool:
    purge = getattr(client, "purge_orphan", None)
    if purge is not None:
        try:
            purge(entry.gid, active=entry.status in {"active", "waiting", "paused"})
            return True
        except Exception:
            logger.info("Purge aria2 ignorée pour %s.", entry.gid)
            return False
    if entry.status in {"active", "waiting", "paused"}:
        from mediatracker.core.pipeline.queue import _forget_aria2_gid

        _forget_aria2_gid(client, entry.gid, delete_files=False)
        return True
    drop = getattr(getattr(client, "client", client), "drop_result", None)
    if drop is not None:
        try:
            drop(entry.gid)
            return True
        except Aria2Error:
            return False
    return False


def _requeue_missing_handles(db: Session, living: set[str], now: datetime) -> int:
    rows = db.scalars(select(Download).where(Download.state.in_(ACTIVE))).all()
    count = 0
    for row in rows:
        if not row.handle or row.handle in living:
            continue
        row.handle = None
        row.state = "pending"
        row.next_retry_at = now
        row.attempt_started_at = None
        row.progress = 0
        row.done_bytes = 0
        row.speed = None
        row.eta_seconds = None
        count += 1
    return count


def _save_session(client) -> None:
    save = getattr(getattr(client, "client", client), "save_session", None)
    if save is not None:
        try:
            save()
        except Aria2Error as exc:
            logger.info("aria2.saveSession ignoré au démarrage : %s.", exc.message)
