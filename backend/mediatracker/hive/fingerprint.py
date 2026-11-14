"""Calcul par blocs des empreintes SHA-256 pour le catalogue Hive (T2).

La reprise utilise ``partial_size`` et ``partial_sha256_hex`` pour valider le
préfixe déjà lu ; un fichier volumineux peut être traité en plusieurs passages.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.file_status import FileStatusIndex
from mediatracker.db.models import HiveFileFingerprint, HiveShareIntent, MediaItem
from mediatracker.db.session import release_sqlite_before_network

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
MAX_CHUNKS_PER_TICK = 32
HASH_CLAIM_STALE = timedelta(minutes=20)


def _apply_pre_write_barrier() -> None:
    """Point d'ancrage test : après validation, avant UPDATE conditionnel."""


@dataclass(frozen=True)
class _FingerprintWorkSnapshot:
    media_item_id: int
    path: str
    size: int
    mtime_ns: int
    partial_start: int
    partial_sha256_hex: str | None


def _stat_fingerprint(path: Path) -> tuple[int, int]:
    st = path.stat()
    size = int(st.st_size)
    mtime_ns = getattr(st, "st_mtime_ns", None)
    if mtime_ns is None:
        mtime_ns = int(st.st_mtime * 1_000_000_000)
    return size, mtime_ns


def _snapshot_still_valid(row: HiveFileFingerprint | None, snapshot: _FingerprintWorkSnapshot) -> bool:
    if row is None:
        return False
    return (
        row.path == snapshot.path
        and int(row.size or 0) == snapshot.size
        and int(row.mtime_ns or 0) == snapshot.mtime_ns
    )


def _classify_disk_against_snapshot(path: Path, snapshot: _FingerprintWorkSnapshot) -> str:
    """``ok`` si le disque correspond au snapshot, ``io_error`` si stat illisible, sinon ``mismatch``."""
    if not path.is_file():
        return "mismatch"
    try:
        size, mtime_ns = _stat_fingerprint(path)
    except OSError:
        return "io_error"
    if size == snapshot.size and mtime_ns == snapshot.mtime_ns and str(path.resolve()) == snapshot.path:
        return "ok"
    return "mismatch"


def _disk_matches_snapshot(path: Path, snapshot: _FingerprintWorkSnapshot) -> bool:
    return _classify_disk_against_snapshot(path, snapshot) == "ok"


def _digest_prefix(path: Path, *, upto: int) -> str | None:
    if upto <= 0:
        return hashlib.sha256().hexdigest()
    h = hashlib.sha256()
    read = 0
    try:
        with path.open("rb") as handle:
            while read < upto:
                chunk = handle.read(min(CHUNK_SIZE, upto - read))
                if not chunk:
                    return None
                h.update(chunk)
                read += len(chunk)
    except OSError:
        return None
    if read != upto:
        return None
    return h.hexdigest()


def recover_fingerprint_db_only(db: Session, *, now: datetime) -> int:
    """À chaque tick : états incohérents et verrous périmés en base uniquement (pas d'I/O disque)."""
    changed = 0
    stale_before = now - HASH_CLAIM_STALE
    rows = list(db.scalars(select(HiveFileFingerprint).where(HiveFileFingerprint.sha256_hex.is_(None))).all())
    for row in rows:
        partial = int(row.partial_size or 0)
        size = int(row.size or 0)
        if partial > size:
            row.partial_size = 0
            row.partial_sha256_hex = None
            changed += 1
        elif partial >= size and size > 0:
            row.partial_size = 0
            row.partial_sha256_hex = None
            changed += 1
        claimed = row.hash_claimed_at
        if claimed is not None:
            if claimed.tzinfo is None:
                claimed = claimed.replace(tzinfo=timezone.utc)
        if claimed is not None and claimed < stale_before:
            row.hash_claimed_at = None
            changed += 1
    if changed:
        db.flush()
    from mediatracker.hive.chunk_digest_store import recover_stale_chunk_digest_claims

    changed += recover_stale_chunk_digest_claims(db, now=now)
    return changed


def validate_fingerprint_prefixes(db: Session, *, now: datetime) -> int:
    """Au démarrage worker : revalide chaque préfixe partiel hors verrou SQLite (un fichier à la fois)."""
    from mediatracker.hive.identity import hive_enabled_in_db

    if not hive_enabled_in_db(db):
        return 0
    changed = 0
    rows = list(
        db.scalars(
            select(HiveFileFingerprint)
            .where(
                HiveFileFingerprint.sha256_hex.is_(None),
                HiveFileFingerprint.partial_size.is_not(None),
                HiveFileFingerprint.partial_size > 0,
                HiveFileFingerprint.partial_sha256_hex.is_not(None),
            )
        ).all()
    )
    for row in rows:
        media_item_id = int(row.media_item_id)
        partial = int(row.partial_size or 0)
        expected = (row.partial_sha256_hex or "").strip()
        path_str = row.path
        if partial <= 0 or not expected or not path_str:
            continue
        db.flush()
        release_sqlite_before_network(db)
        prefix = _digest_prefix(Path(path_str), upto=partial)
        fresh = _load_fingerprint_row_fresh(db, media_item_id)
        if fresh is None or not (fresh.partial_sha256_hex or "").strip():
            continue
        if prefix is not None and prefix == fresh.partial_sha256_hex:
            continue
        item = db.get(MediaItem, media_item_id)
        size = int(fresh.size or 0)
        mtime_ns = int(fresh.mtime_ns or 0)
        _invalidate_fingerprint_for_file_change(
            db,
            fresh,
            item,
            message="Le fichier a changé pendant le calcul ; le calcul reprend depuis le début.",
            size=size,
            mtime_ns=mtime_ns,
            moment=now,
        )
        changed += 1
        db.flush()
    return changed


def _hash_file_budget(
    path: Path,
    *,
    partial_start: int,
    size: int,
    moment_unused: datetime,
    expected_prefix_hex: str | None = None,
) -> tuple[int, hashlib._Hash]:
    """Lecture disque hors transaction SQLite (aucune écriture ORM)."""
    del moment_unused
    h = hashlib.sha256()
    read_upto = 0
    partial = partial_start
    with path.open("rb") as f:
        while read_upto < partial:
            chunk = f.read(min(CHUNK_SIZE, partial - read_upto))
            if not chunk:
                partial = 0
                break
            h.update(chunk)
            read_upto += len(chunk)

        if partial > 0 and expected_prefix_hex is not None and h.hexdigest() != expected_prefix_hex:
            return partial_start, hashlib.sha256()

        budget = MAX_CHUNKS_PER_TICK * CHUNK_SIZE
        used = 0
        while read_upto < size and used < budget:
            chunk = f.read(min(CHUNK_SIZE, size - read_upto))
            if not chunk:
                break
            h.update(chunk)
            read_upto += len(chunk)
            used += len(chunk)
    return read_upto, h


def _load_fingerprint_row_fresh(db: Session, media_item_id: int) -> HiveFileFingerprint | None:
    """Relecture DB indépendante du cache d'identité ORM (écritures concurrentes)."""
    return db.scalar(
        select(HiveFileFingerprint)
        .where(HiveFileFingerprint.media_item_id == media_item_id)
        .execution_options(populate_existing=True)
    )


def _invalidate_fingerprint_for_file_change(
    db: Session,
    row: HiveFileFingerprint,
    item: MediaItem | None,
    *,
    message: str,
    size: int,
    mtime_ns: int,
    moment: datetime,
) -> None:
    row.partial_size = 0
    row.partial_sha256_hex = None
    row.sha256_hex = None
    row.hash_error = message
    row.hash_claimed_at = None
    row.updated_at = moment
    from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

    clear_chunk_digest_state(row)
    if item is not None and item.id is not None:
        from mediatracker.hive.fingerprint_journal import record_fingerprint_error

        record_fingerprint_error(db, item, message=message, size=size, mtime_ns=mtime_ns, when=moment)


def _apply_hash_result(
    db: Session,
    snapshot: _FingerprintWorkSnapshot,
    *,
    read_upto: int,
    digest: hashlib._Hash,
    moment: datetime,
    chunk_digests: list[str] | None = None,
) -> None:
    item = db.get(MediaItem, snapshot.media_item_id)
    path = Path(snapshot.path)
    disk = _classify_disk_against_snapshot(path, snapshot)
    if disk == "io_error":
        return
    if disk == "mismatch":
        row = _load_fingerprint_row_fresh(db, snapshot.media_item_id)
        if row is not None and item is not None:
            try:
                size, mtime_ns = _stat_fingerprint(path)
            except OSError:
                size, mtime_ns = snapshot.size, snapshot.mtime_ns
            _invalidate_fingerprint_for_file_change(
                db,
                row,
                item,
                message="Le fichier a été modifié ou supprimé pendant le calcul d'empreinte.",
                size=size,
                mtime_ns=mtime_ns,
                moment=moment,
            )
            db.flush()
        return
    row = _load_fingerprint_row_fresh(db, snapshot.media_item_id)
    if not _snapshot_still_valid(row, snapshot):
        return

    if read_upto <= snapshot.partial_start:
        return

    if read_upto >= snapshot.size:
        values: dict = {
            "sha256_hex": digest.hexdigest(),
            "partial_size": snapshot.size,
            "partial_sha256_hex": digest.hexdigest(),
            "hash_error": None,
            "hash_claimed_at": None,
            "updated_at": moment,
        }
    else:
        values = {
            "partial_size": read_upto,
            "partial_sha256_hex": digest.hexdigest(),
            "hash_error": None,
            "updated_at": moment,
        }

    _apply_pre_write_barrier()

    result = db.execute(
        update(HiveFileFingerprint)
        .where(
            HiveFileFingerprint.media_item_id == snapshot.media_item_id,
            HiveFileFingerprint.path == snapshot.path,
            HiveFileFingerprint.size == snapshot.size,
            HiveFileFingerprint.mtime_ns == snapshot.mtime_ns,
            func.coalesce(HiveFileFingerprint.partial_size, 0) == snapshot.partial_start,
            HiveFileFingerprint.sha256_hex.is_(None),
        )
        .values(**values)
    )
    if int(result.rowcount or 0) != 1:
        return
    if read_upto >= snapshot.size:
        row_after = _load_fingerprint_row_fresh(db, snapshot.media_item_id)
        if row_after is not None and chunk_digests is not None:
            from mediatracker.hive.chunk_digest_store import persist_chunk_digests_row

            try:
                persist_chunk_digests_row(db, row_after, digests=chunk_digests)
            except ValueError:
                from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

                clear_chunk_digest_state(row_after)
                db.flush()
    if read_upto >= snapshot.size and item is not None:
        from mediatracker.hive.fingerprint_journal import record_fingerprint_done

        record_fingerprint_done(
            db, item, sha256_hex=digest.hexdigest(), size=snapshot.size, when=moment
        )


def has_pending_hive_fingerprint(db: Session) -> bool:
    """Empreinte incomplète ou file dédiée non vide (sommeil worker / API)."""
    from mediatracker.hive.chunk_digest_store import count_pending_chunk_backfill
    from mediatracker.hive.fingerprint_queue import queue_has_work

    if queue_has_work(db):
        return True
    if count_pending_chunk_backfill(db) > 0:
        return True
    row = db.scalar(
        select(HiveFileFingerprint.media_item_id)
        .join(HiveShareIntent, HiveShareIntent.media_item_id == HiveFileFingerprint.media_item_id)
        .where(HiveFileFingerprint.sha256_hex.is_(None))
        .limit(1)
    )
    return row is not None


def _maybe_wake_fingerprint_worker(db: Session, settings: Settings) -> None:
    from mediatracker.hive.fingerprint_wake import wake_fingerprint_daemon

    wake_fingerprint_daemon(db, settings, commit=False)


def hive_fingerprint_tick(db: Session, settings: Settings, *, now: datetime | None = None) -> None:
    """Compatibilité tests : un pas du processus dédié (non appelé par le worker principal)."""
    from mediatracker.hive.fingerprint_daemon import fingerprint_daemon_tick

    fingerprint_daemon_tick(db, settings, now=now)


def record_catalog_fingerprint_for_local_file(
    db: Session,
    item: MediaItem,
    path: Path,
    *,
    sha256_hex: str,
    now: datetime,
) -> None:
    """Enregistre l'empreinte fichier déjà vérifiée (ex. réception Hive) sans partage ni file SHA."""
    if item.id is None:
        return
    catalog_sha = (sha256_hex or "").strip().lower()
    if len(catalog_sha) != 64:
        return
    try:
        int(catalog_sha, 16)
    except ValueError:
        return
    if not path.is_file():
        return
    try:
        size, mtime_ns = _stat_fingerprint(path)
    except OSError:
        return
    resolved = str(path.resolve())
    from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state
    from mediatracker.hive.fingerprint_queue import dequeue_completed

    row = db.get(HiveFileFingerprint, item.id)
    if row is None:
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=resolved,
                size=size,
                mtime_ns=mtime_ns,
                sha256_hex=catalog_sha,
                partial_size=size,
                partial_sha256_hex=catalog_sha,
                hash_error=None,
                hash_claimed_at=None,
                updated_at=now,
            )
        )
    else:
        identity_changed = (
            row.path != resolved or int(row.size or 0) != size or int(row.mtime_ns or 0) != mtime_ns
        )
        sha_changed = (row.sha256_hex or "").strip().lower() != catalog_sha
        if identity_changed or sha_changed:
            clear_chunk_digest_state(row)
        row.path = resolved
        row.size = size
        row.mtime_ns = mtime_ns
        row.sha256_hex = catalog_sha
        row.partial_size = size
        row.partial_sha256_hex = catalog_sha
        row.hash_error = None
        row.hash_claimed_at = None
        row.updated_at = now
    dequeue_completed(db, int(item.id))


def ensure_fingerprint_row_for_item(db: Session, settings: Settings, item: MediaItem, *, now: datetime) -> bool:
    """Crée ou met à jour une ligne de hachage si l'épisode a un fichier local présent.

    Retourne True si une empreinte est encore à calculer (réveil worker recommandé).
    """
    if item.id is None:
        return False
    from mediatracker.hive.share_policy import item_wants_hive_share

    if not item_wants_hive_share(db, item.id):
        return False
    idx = FileStatusIndex.load(db, [item])
    if idx.for_item(item) != "present":
        row = db.get(HiveFileFingerprint, item.id)
        if row is not None:
            db.delete(row)
        return False

    path_str = item.file_path
    if not path_str:
        return False
    path = Path(path_str)
    if not path.is_file():
        return False

    try:
        size, mtime_ns = _stat_fingerprint(path)
    except OSError:
        return False

    pending = False
    row = db.get(HiveFileFingerprint, item.id)
    if row is None:
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(path.resolve()),
                size=size,
                mtime_ns=mtime_ns,
                updated_at=now,
            )
        )
        pending = True
    elif row.path != str(path.resolve()) or row.size != size or row.mtime_ns != mtime_ns:
        from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

        row.path = str(path.resolve())
        row.size = size
        row.mtime_ns = mtime_ns
        row.sha256_hex = None
        row.partial_size = 0
        row.partial_sha256_hex = None
        row.hash_error = None
        row.hash_claimed_at = None
        clear_chunk_digest_state(row)
        row.updated_at = now
        pending = True
    elif not (row.sha256_hex or "").strip():
        pending = True

    if pending:
        from mediatracker.hive.fingerprint_queue import enqueue_fingerprint_work

        enqueue_fingerprint_work(db, int(item.id), now=now)
        _maybe_wake_fingerprint_worker(db, settings)
    return pending
