"""Processus dédié : un fichier empreinté à la fois, file SQLite, état SHA en mémoire."""

from __future__ import annotations

import fcntl
import logging
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings, get_settings
from mediatracker.core.storage import storage_layout
from mediatracker.core.worker_wake import WorkerWaiter
from mediatracker.db.models import MediaItem
from mediatracker.db.session import commit_retry, create_session_factory, release_sqlite_before_network
from mediatracker.hive.catalog import sync_hive_catalog_item
from mediatracker.hive.fingerprint import (
    _apply_hash_result,
    _classify_disk_against_snapshot,
    _FingerprintWorkSnapshot,
    _invalidate_fingerprint_for_file_change,
    _load_fingerprint_row_fresh,
    _stat_fingerprint,
    recover_fingerprint_db_only,
    validate_fingerprint_prefixes,
)
from mediatracker.hive.fingerprint_queue import (
    abandon_orphan_queue_work,
    backfill_queue_from_incomplete,
    claim_next_pending,
    dequeue_completed,
    drop_queue_for_media_items,
    mark_queue_failed,
    queue_has_work,
    recover_fingerprint_queue,
    refresh_running_claim_lease,
    release_running_to_pending,
)
from mediatracker.hive.fingerprint_stream import READ_BUDGET_BYTES, StreamingSha256Session
from mediatracker.hive.fingerprint_wake import fingerprint_fifo_path
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.share_policy import abandon_orphan_fingerprint_work, item_wants_hive_share
from mediatracker.security.logging import configure_logging

logger = logging.getLogger(__name__)

ACTIVE_TICK_SECONDS = 2.0
IDLE_TICK_SECONDS = 30.0
MIN_SLEEP_SECONDS = 1.0
MAX_SLEEP_SECONDS = 90.0
_BUSY_PRESSURE = 0.55
_VERY_BUSY_PRESSURE = 0.85
_CALM_PRESSURE = 0.25
MAX_TRANSIENT_CLAIM_ERRORS = 5
_LOCK = None
_transient_claim_errors: dict[int, int] = {}

_active_session: StreamingSha256Session | None = None
_active_item_id: int | None = None
_active_snapshot: _FingerprintWorkSnapshot | None = None


def clear_active_session() -> None:
    global _active_session, _active_item_id, _active_snapshot
    _active_session = None
    _active_item_id = None
    _active_snapshot = None


def active_session_for_tests() -> StreamingSha256Session | None:
    return _active_session


def _cancel_active_for_items(cancelled: set[int]) -> None:
    global _active_session, _active_item_id, _active_snapshot

    if _active_item_id is not None and _active_item_id in cancelled:
        clear_active_session()


def _sync_orphan_fingerprint_work(db: Session, *, moment: datetime) -> set[int]:
    abandon_orphan_fingerprint_work(db)
    removed_queue = abandon_orphan_queue_work(db, now=moment)
    return set(removed_queue)


def _release_job_transient(
    db: Session,
    media_item_id: int,
    *,
    reason: str,
    moment: datetime,
) -> None:
    global _transient_claim_errors

    attempts = _transient_claim_errors.get(media_item_id, 0) + 1
    _transient_claim_errors[media_item_id] = attempts
    if attempts >= MAX_TRANSIENT_CLAIM_ERRORS:
        mark_queue_failed(db, media_item_id, reason=reason, now=moment)
        _transient_claim_errors.pop(media_item_id, None)
        _cancel_active_for_items({media_item_id})
        return
    release_running_to_pending(db, media_item_id, now=moment)
    row = _load_fingerprint_row_fresh(db, media_item_id)
    if row is not None:
        row.hash_claimed_at = None
        db.flush()
    _cancel_active_for_items({media_item_id})


def fingerprint_daemon_tick(db: Session, settings: Settings, *, now: datetime | None = None) -> None:
    """Un pas de calcul (un budget disque max) pour le consommateur unique."""
    global _active_session, _active_item_id, _active_snapshot

    moment = now or datetime.now(timezone.utc)
    if not hive_enabled_in_db(db):
        if _active_item_id is not None:
            release_running_to_pending(db, _active_item_id, now=moment)
            clear_active_session()
        return

    cancelled = _sync_orphan_fingerprint_work(db, moment=moment)
    _cancel_active_for_items(cancelled)
    recover_fingerprint_db_only(db, now=moment)
    if _active_item_id is not None:
        refresh_running_claim_lease(db, _active_item_id, now=moment)
    recover_fingerprint_queue(db, now=moment)

    if _active_session is not None and _active_item_id is not None and _active_snapshot is not None:
        if not _should_continue_active_item(db, _active_item_id):
            release_running_to_pending(db, _active_item_id, now=moment)
            clear_active_session()
        elif not _continue_active(db, settings, moment=moment):
            return
        else:
            return

    if not queue_has_work(db):
        backfill_queue_from_incomplete(db, now=moment)

    media_item_id = claim_next_pending(db, now=moment)
    if media_item_id is None:
        return

    if not _start_job(db, settings, media_item_id, moment=moment):
        return
    _continue_active(db, settings, moment=moment)


def _should_continue_active_item(db: Session, media_item_id: int) -> bool:
    if not item_wants_hive_share(db, media_item_id):
        return False
    if _load_fingerprint_row_fresh(db, media_item_id) is None:
        return False
    return True


def _start_job(db: Session, settings: Settings, media_item_id: int, *, moment: datetime) -> bool:
    global _active_session, _active_item_id, _active_snapshot, _transient_claim_errors

    if not item_wants_hive_share(db, media_item_id):
        drop_queue_for_media_items(db, {media_item_id})
        db.flush()
        return False

    row = _load_fingerprint_row_fresh(db, media_item_id)
    item = db.get(MediaItem, media_item_id)
    if row is None or item is None:
        mark_queue_failed(db, media_item_id, reason="Empreinte absente pour l'élément en file.", now=moment)
        db.flush()
        return False

    path = Path(row.path)
    layout = storage_layout(db, settings)
    trash_resolved = layout.trash_dir.resolve()
    if not path.is_file():
        mark_queue_failed(db, media_item_id, reason="Fichier local absent.", now=moment)
        if item is not None:
            _invalidate_fingerprint_for_file_change(
                db,
                row,
                item,
                message="Fichier local absent.",
                size=int(row.size or 0),
                mtime_ns=int(row.mtime_ns or 0),
                moment=moment,
            )
        db.flush()
        return False

    try:
        resolved = path.resolve()
        if resolved == trash_resolved or str(resolved).startswith(str(trash_resolved) + "/"):
            mark_queue_failed(db, media_item_id, reason="Fichier en corbeille.", now=moment)
            db.delete(row)
            db.flush()
            return False
        resolved_path = str(resolved)
    except OSError:
        _release_job_transient(db, media_item_id, reason="Fichier inaccessible (transitoire).", moment=moment)
        return False

    try:
        size, mtime_ns = _stat_fingerprint(path)
    except OSError:
        _release_job_transient(db, media_item_id, reason="Stat fichier impossible (transitoire).", moment=moment)
        return False

    if row.path != resolved_path or int(row.size or 0) != size or int(row.mtime_ns or 0) != mtime_ns:
        row.path = resolved_path
        row.size = size
        row.mtime_ns = mtime_ns
        row.partial_size = 0
        row.partial_sha256_hex = None
        row.sha256_hex = None
        row.hash_error = None
        row.hash_claimed_at = None
        from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

        clear_chunk_digest_state(row)
        row.updated_at = moment
        db.flush()

    partial = int(row.partial_size or 0)
    if partial > size:
        partial = 0
        row.partial_size = 0
        row.partial_sha256_hex = None
        db.flush()

    if partial == 0:
        from mediatracker.hive.fingerprint_journal import record_fingerprint_started

        record_fingerprint_started(db, item, size=size, mtime_ns=mtime_ns, when=moment)

    row.hash_claimed_at = moment
    db.flush()

    expected = (row.partial_sha256_hex or "").strip() or None
    release_sqlite_before_network(db)
    session = StreamingSha256Session.resume_from_disk(
        path,
        size=size,
        partial=partial,
        expected_prefix_hex=expected if partial > 0 else None,
    )
    if session is None and partial > 0:
        _invalidate_fingerprint_for_file_change(
            db,
            row,
            item,
            message="Le préfixe sur disque ne correspond plus à la reprise ; nouveau calcul.",
            size=size,
            mtime_ns=mtime_ns,
            moment=moment,
        )
        row = _load_fingerprint_row_fresh(db, media_item_id)
        if row is None:
            mark_queue_failed(db, media_item_id, reason="Empreinte invalidée.", now=moment)
            db.flush()
            return False
        partial = 0
        release_sqlite_before_network(db)
        session = StreamingSha256Session.open_fresh(path, size=size)
    elif session is None:
        mark_queue_failed(db, media_item_id, reason="Ouverture fichier impossible.", now=moment)
        db.flush()
        return False

    _active_session = session
    _active_item_id = media_item_id
    _active_snapshot = _FingerprintWorkSnapshot(
        media_item_id=media_item_id,
        path=resolved_path,
        size=size,
        mtime_ns=mtime_ns,
        partial_start=partial,
        partial_sha256_hex=expected,
    )
    return True


def _continue_active(db: Session, settings: Settings, *, moment: datetime) -> bool:
    global _active_session, _active_item_id, _active_snapshot

    assert _active_session is not None and _active_item_id is not None and _active_snapshot is not None

    path = Path(_active_snapshot.path)
    disk = _classify_disk_against_snapshot(path, _active_snapshot)
    if disk == "io_error":
        _release_job_transient(
            db,
            _active_item_id,
            reason="Stat fichier impossible (transitoire).",
            moment=moment,
        )
        return False
    if disk == "mismatch":
        row = _load_fingerprint_row_fresh(db, _active_item_id)
        item = db.get(MediaItem, _active_item_id)
        if row is not None and item is not None:
            try:
                size, mtime_ns = _stat_fingerprint(path)
            except OSError:
                size, mtime_ns = _active_snapshot.size, _active_snapshot.mtime_ns
            _invalidate_fingerprint_for_file_change(
                db,
                row,
                item,
                message="Le fichier a été modifié ou supprimé pendant le calcul d'empreinte.",
                size=size,
                mtime_ns=mtime_ns,
                moment=moment,
            )
        mark_queue_failed(
            db,
            _active_item_id,
            reason="Fichier modifié ou absent pendant le calcul.",
            now=moment,
        )
        clear_active_session()
        db.flush()
        return False

    release_sqlite_before_network(db)
    read_upto = _active_session.read_budget(READ_BUDGET_BYTES)
    if (
        _active_session.bytes_read_last_budget == 0
        and read_upto < _active_snapshot.size
    ):
        _release_job_transient(
            db,
            _active_item_id,
            reason="Lecture fichier impossible (transitoire).",
            moment=moment,
        )
        return False
    if _active_session.bytes_read_last_budget > 0:
        _transient_claim_errors.pop(_active_item_id, None)
    digest = _active_session.hasher

    snapshot = _FingerprintWorkSnapshot(
        media_item_id=_active_snapshot.media_item_id,
        path=_active_snapshot.path,
        size=_active_snapshot.size,
        mtime_ns=_active_snapshot.mtime_ns,
        partial_start=_active_snapshot.partial_start,
        partial_sha256_hex=_active_snapshot.partial_sha256_hex,
    )
    _apply_hash_result(db, snapshot, read_upto=read_upto, digest=digest, moment=moment)
    fresh = _load_fingerprint_row_fresh(db, _active_item_id)
    if fresh is not None and (fresh.sha256_hex or "").strip():
        fresh.hash_claimed_at = None
        sync_hive_catalog_item(db, settings, _active_item_id, now=moment)
        dequeue_completed(db, _active_item_id)
        _transient_claim_errors.pop(_active_item_id, None)
        clear_active_session()
        db.flush()
        return True

    if read_upto > _active_snapshot.partial_start:
        _active_snapshot = _FingerprintWorkSnapshot(
            media_item_id=_active_snapshot.media_item_id,
            path=_active_snapshot.path,
            size=_active_snapshot.size,
            mtime_ns=_active_snapshot.mtime_ns,
            partial_start=read_upto,
            partial_sha256_hex=digest.hexdigest(),
        )
    elif fresh is not None and int(fresh.partial_size or 0) >= _active_snapshot.size:
        fresh.hash_claimed_at = None

    return True


def hive_fingerprint_tick(db: Session, settings: Settings, *, now: datetime | None = None) -> None:
    """Point d'entrée stable pour les tests : délègue au daemon (hors worker principal)."""
    fingerprint_daemon_tick(db, settings, now=now)


def compute_fingerprint_sleep(db: Session) -> float:
    from mediatracker.hive.chunk_digest_store import count_pending_chunk_backfill
    from mediatracker.hive.fingerprint_load import system_pressure_score

    has_work = (
        _active_session is not None
        or queue_has_work(db)
        or count_pending_chunk_backfill(db) > 0
    )
    base = ACTIVE_TICK_SECONDS if has_work else IDLE_TICK_SECONDS
    pressure = system_pressure_score()
    if pressure is None:
        return base
    if pressure >= _VERY_BUSY_PRESSURE:
        return min(MAX_SLEEP_SECONDS, base * 4.0)
    if pressure >= _BUSY_PRESSURE:
        return min(MAX_SLEEP_SECONDS, base * 2.0)
    if pressure <= _CALM_PRESSURE and has_work:
        return max(MIN_SLEEP_SECONDS, base * 0.5)
    if pressure <= _CALM_PRESSURE:
        return min(IDLE_TICK_SECONDS, max(MIN_SLEEP_SECONDS, base * 0.75))
    return base


def _claim_fingerprint_daemon(config_dir: Path) -> bool:
    global _LOCK
    config_dir.mkdir(parents=True, exist_ok=True)
    handle = (config_dir / "fingerprint.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK = handle
    return True


def _fingerprint_waiter(config_dir: Path, stop: threading.Event) -> WorkerWaiter:
    waiter = WorkerWaiter(config_dir, stop)
    waiter._fifo = fingerprint_fifo_path(config_dir=config_dir)
    return waiter


def main() -> None:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        Path(settings.config_dir) / "logs",
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    if not _claim_fingerprint_daemon(Path(settings.config_dir)):
        logger.error("Un processus empreintes est déjà en cours. Arrêt.")
        return
    _engine, factory = create_session_factory(settings)
    stop = threading.Event()

    def _stop(signum, _frame) -> None:
        logger.info("Signal %s reçu, arrêt du processus empreintes.", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info(
        "Processus empreintes Hive démarré (tick actif %s s, repos %s s).",
        int(ACTIVE_TICK_SECONDS),
        int(IDLE_TICK_SECONDS),
    )
    try:
        boot_db = factory()
        try:
            boot_moment = datetime.now(timezone.utc)
            recover_fingerprint_db_only(boot_db, now=boot_moment)
            recover_fingerprint_queue(boot_db, now=boot_moment)
            if hive_enabled_in_db(boot_db):
                validate_fingerprint_prefixes(boot_db, now=boot_moment)
                backfill_queue_from_incomplete(boot_db, now=boot_moment)
            commit_retry(boot_db)
        finally:
            boot_db.close()
    except Exception:
        logger.exception("Reprise file empreintes au démarrage échouée ; le processus continue.")
    config_dir = Path(settings.config_dir)
    waiter = _fingerprint_waiter(config_dir, stop)
    waiter.start()
    while not stop.is_set():
        try:
            db = factory()
            try:
                fingerprint_daemon_tick(db, settings)
                commit_retry(db)
            finally:
                db.close()
        except Exception:
            logger.exception("Passage empreintes échoué.")
        sleep_db = factory()
        try:
            pause = compute_fingerprint_sleep(sleep_db)
        finally:
            sleep_db.close()
        waiter.wait(pause)


if __name__ == "__main__":
    main()
