"""Backfill borné des digests morceaux pour empreintes legacy (processus empreintes)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveFileFingerprint
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.hive.chunk_digest import TRANSFER_CHUNK_SIZE
from mediatracker.hive.chunk_digest_store import (
    HIVE_CHUNK_DIGESTS_REQUIRED,
    ChunkVersionSnapshot,
    clear_chunk_digest_state,
    list_backfill_candidate_ids,
    load_fingerprint_row_fresh,
    persist_chunk_digests_cas,
    persist_chunk_partial_cas,
    recover_stale_chunk_digest_claims,
)
from mediatracker.hive.chunk_digest_stream import TransferChunkDigestAccumulator
from mediatracker.hive.fingerprint import _stat_fingerprint
from mediatracker.hive.fingerprint_stream import CHUNK_SIZE, READ_BUDGET_BYTES
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.share_policy import item_wants_hive_share

logger = logging.getLogger(__name__)

_active_chunk_item_id: int | None = None
_active_accumulator: TransferChunkDigestAccumulator | None = None
_active_size: int = 0
_active_snapshot: _ChunkBackfillSnapshot | None = None


@dataclass(frozen=True)
class _ChunkBackfillSnapshot:
    media_item_id: int
    sha256_hex: str
    size: int
    mtime_ns: int


def clear_active_chunk_session() -> None:
    global _active_chunk_item_id, _active_accumulator, _active_size, _active_snapshot
    _active_chunk_item_id = None
    _active_accumulator = None
    _active_size = 0
    _active_snapshot = None


def release_chunk_digest_claim(db: Session, media_item_id: int, *, now: datetime) -> None:
    row = db.get(HiveFileFingerprint, media_item_id)
    if row is not None:
        row.chunk_digest_claimed_at = None
        db.flush()
    if _active_chunk_item_id == media_item_id:
        clear_active_chunk_session()


def abort_chunk_backfill_for_fingerprint_work(db: Session, *, now: datetime) -> None:
    """Libère une session backfill morceaux avant de reprendre un job SHA."""
    if _active_chunk_item_id is None:
        return
    release_chunk_digest_claim(db, _active_chunk_item_id, now=now)


def _snapshot_from_row(row: HiveFileFingerprint) -> _ChunkBackfillSnapshot:
    return _ChunkBackfillSnapshot(
        media_item_id=int(row.media_item_id),
        sha256_hex=(row.sha256_hex or "").strip(),
        size=int(row.size or 0),
        mtime_ns=int(row.mtime_ns or 0),
    )


def _snapshot_still_valid(row: HiveFileFingerprint, snap: _ChunkBackfillSnapshot) -> bool:
    if int(row.media_item_id) != snap.media_item_id:
        return False
    sha = (row.sha256_hex or "").strip()
    if not sha or sha != snap.sha256_hex:
        return False
    if int(row.size or 0) != snap.size or int(row.mtime_ns or 0) != snap.mtime_ns:
        return False
    return True


def _claim_next(db: Session, *, now: datetime) -> int | None:
    for media_item_id in list_backfill_candidate_ids(db, limit=32, now=now):
        result = db.execute(
            update(HiveFileFingerprint)
            .where(
                HiveFileFingerprint.media_item_id == int(media_item_id),
                HiveFileFingerprint.sha256_hex.is_not(None),
                HiveFileFingerprint.chunk_digest_claimed_at.is_(None),
            )
            .values(chunk_digest_claimed_at=now, updated_at=now)
        )
        if int(result.rowcount or 0) == 1:
            db.flush()
            return int(media_item_id)
    return None


def _discard_stale_partial_state(row: HiveFileFingerprint) -> None:
    if not row.chunk_digest_partial_json:
        return
    sha = (row.sha256_hex or "").strip().lower()
    bound = (row.chunk_digests_sha256_hex or "").strip().lower()
    if bound and sha and bound != sha:
        clear_chunk_digest_state(row)


def _restore_accumulator(row: HiveFileFingerprint) -> TransferChunkDigestAccumulator:
    _discard_stale_partial_state(row)
    size = int(row.size or 0)
    offset = int(row.chunk_digest_offset or 0)
    acc = TransferChunkDigestAccumulator(file_size=size)
    partial: list[str] = []
    if row.chunk_digest_partial_json:
        try:
            raw = json.loads(row.chunk_digest_partial_json)
            if isinstance(raw, list):
                partial = [str(x) for x in raw if isinstance(x, str)]
        except json.JSONDecodeError:
            partial = []
    if not acc.try_restore(completed=partial, file_offset=offset):
        clear_chunk_digest_state(row)
        row.chunk_digest_offset = 0
        row.chunk_digest_partial_json = None
    return acc


def _should_persist_partial(acc: TransferChunkDigestAccumulator, *, file_size: int) -> bool:
    off = acc.file_offset
    if off >= file_size:
        return True
    return off % TRANSFER_CHUNK_SIZE == 0


def _persist_partial(
    db: Session,
    snap: _ChunkBackfillSnapshot,
    acc: TransferChunkDigestAccumulator,
    *,
    file_size: int,
    moment: datetime,
) -> bool:
    if not _should_persist_partial(acc, file_size=file_size):
        return True
    payload = json.dumps(acc.completed, separators=(",", ":"))
    version = ChunkVersionSnapshot(
        media_item_id=snap.media_item_id,
        sha256_hex=snap.sha256_hex,
        size=snap.size,
        mtime_ns=snap.mtime_ns,
    )
    return persist_chunk_partial_cas(
        db, version, offset=acc.file_offset, partial_json=payload, moment=moment
    )


def _start_job(db: Session, media_item_id: int, *, moment: datetime) -> bool:
    global _active_chunk_item_id, _active_accumulator, _active_size, _active_snapshot

    row = db.get(HiveFileFingerprint, media_item_id)
    if row is None or not (row.sha256_hex or "").strip():
        release_chunk_digest_claim(db, media_item_id, now=moment)
        return False
    path = Path(row.path)
    try:
        size, mtime_ns = _stat_fingerprint(path)
    except OSError:
        release_chunk_digest_claim(db, media_item_id, now=moment)
        return False
    if int(row.size or 0) != size or int(row.mtime_ns or 0) != mtime_ns:
        clear_chunk_digest_state(row)
        row.updated_at = moment
        db.flush()
        release_chunk_digest_claim(db, media_item_id, now=moment)
        return False
    if int(row.chunk_digest_offset or 0) > size:
        clear_chunk_digest_state(row)
        db.flush()
    _active_chunk_item_id = media_item_id
    _active_size = size
    _active_snapshot = _snapshot_from_row(row)
    _active_accumulator = _restore_accumulator(row)
    return True


def _read_budget(acc: TransferChunkDigestAccumulator, path: Path, *, budget: int) -> int:
    offset = acc.file_offset
    if offset >= _active_size:
        return 0
    read_total = 0
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            while read_total < budget and acc.file_offset < _active_size:
                to_read = min(CHUNK_SIZE, _active_size - acc.file_offset, budget - read_total)
                block = handle.read(to_read)
                if not block:
                    break
                acc.feed(block)
                read_total += len(block)
    except OSError:
        return read_total
    return read_total


def _run_active_chunk_budget(db: Session, *, moment: datetime) -> bool:
    global _active_chunk_item_id, _active_accumulator, _active_snapshot
    if _active_chunk_item_id is None or _active_accumulator is None or _active_snapshot is None:
        return False
    row = load_fingerprint_row_fresh(db, _active_chunk_item_id)
    if row is None or not item_wants_hive_share(db, _active_chunk_item_id):
        release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return True
    if not _snapshot_still_valid(row, _active_snapshot):
        clear_chunk_digest_state(row)
        row.updated_at = moment
        db.flush()
        release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return True
    path = Path(row.path)
    release_sqlite_before_network(db)
    read_n = _read_budget(_active_accumulator, path, budget=READ_BUDGET_BYTES)
    if read_n == 0 and _active_accumulator.file_offset < int(row.size or 0):
        release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return True
    if _active_accumulator.is_complete():
        fresh = load_fingerprint_row_fresh(db, _active_chunk_item_id)
        if fresh is None or not _snapshot_still_valid(fresh, _active_snapshot):
            if fresh is not None:
                clear_chunk_digest_state(fresh)
                db.flush()
            release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
            return True
        snap = ChunkVersionSnapshot(
            media_item_id=_active_snapshot.media_item_id,
            sha256_hex=_active_snapshot.sha256_hex,
            size=_active_snapshot.size,
            mtime_ns=_active_snapshot.mtime_ns,
        )
        try:
            ok = persist_chunk_digests_cas(db, snap, digests=_active_accumulator.completed, moment=moment)
        except ValueError:
            if fresh is not None:
                clear_chunk_digest_state(fresh)
                db.flush()
            release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
            return True
        if not ok and fresh is not None:
            clear_chunk_digest_state(fresh)
            db.flush()
        release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return True
    if not _persist_partial(
        db, _active_snapshot, _active_accumulator, file_size=int(row.size or 0), moment=moment
    ):
        clear_chunk_digest_state(row)
        db.flush()
        release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return True
    return True


def chunk_digest_backfill_tick(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    recurse: bool = True,
) -> bool:
    """Un budget lecture max ; retourne True si du travail a été fait."""
    global _active_chunk_item_id, _active_accumulator, _active_snapshot

    moment = now or datetime.now(timezone.utc)
    recover_stale_chunk_digest_claims(db, now=moment)
    if not HIVE_CHUNK_DIGESTS_REQUIRED:
        if _active_chunk_item_id is not None:
            release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return False
    if not hive_enabled_in_db(db):
        if _active_chunk_item_id is not None:
            release_chunk_digest_claim(db, _active_chunk_item_id, now=moment)
        return False

    if _active_chunk_item_id is not None and _active_accumulator is not None and _active_snapshot is not None:
        return _run_active_chunk_budget(db, moment=moment)

    media_item_id = _claim_next(db, now=moment)
    if media_item_id is None:
        return False
    if not _start_job(db, media_item_id, moment=moment):
        return True
    if recurse:
        return chunk_digest_backfill_tick(db, settings, now=moment, recurse=recurse)
    return _run_active_chunk_budget(db, moment=moment)
