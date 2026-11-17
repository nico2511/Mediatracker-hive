"""Persistance et validation des empreintes morceaux 16 Mio (côté émetteur)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveFileFingerprint, HiveShareIntent
from mediatracker.hive.chunk_digest import TRANSFER_CHUNK_SIZE, parse_chunk_digests_header
from mediatracker.hive.fingerprint import HIVE_CLAIM_STALE
from mediatracker.hive.share_policy import item_wants_hive_share

logger = logging.getLogger(__name__)

# LOT4+ : seul aria2 + fetch-grant consomme GET /hive/files ; pas de morceaux requis.
HIVE_CHUNK_DIGESTS_REQUIRED = False

HIVE_CHUNKS_PENDING_HEADER = "Hive-Chunks-Pending"


@dataclass(frozen=True)
class ChunkVersionSnapshot:
    media_item_id: int
    sha256_hex: str
    size: int
    mtime_ns: int


class ChunkDigestsNotReady(Exception):
    """Digests morceaux absents ou en cours de préparation (pas d'I/O dans le handler)."""


def expected_chunk_count(file_size: int) -> int:
    if file_size <= 0:
        return 0
    return (file_size + TRANSFER_CHUNK_SIZE - 1) // TRANSFER_CHUNK_SIZE


def _parse_digest_list(raw: str | None) -> list[str] | None:
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        return None
    return data


def chunk_digests_row_valid(row: HiveFileFingerprint | None) -> list[str] | None:
    """Retourne les digests si la ligne est cohérente avec la version fichier."""
    if row is None:
        return None
    sha = (row.sha256_hex or "").strip().lower()
    if not sha:
        return None
    bound = (row.chunk_digests_sha256_hex or "").strip().lower()
    if bound and bound != sha:
        return None
    size = int(row.size or 0)
    digests = _parse_digest_list(row.chunk_digests_json)
    if digests is None:
        return None
    if len(digests) != expected_chunk_count(size):
        return None
    if int(row.chunk_digest_offset or 0) != size:
        return None
    return digests


def clear_chunk_digest_state(row: HiveFileFingerprint) -> None:
    row.chunk_digests_json = None
    row.chunk_digest_offset = 0
    row.chunk_digest_partial_json = None
    row.chunk_digest_claimed_at = None
    row.chunk_digests_sha256_hex = None


def load_fingerprint_row_fresh(db: Session, media_item_id: int) -> HiveFileFingerprint | None:
    return db.scalar(
        select(HiveFileFingerprint)
        .where(HiveFileFingerprint.media_item_id == media_item_id)
        .execution_options(populate_existing=True)
    )


def persist_chunk_digests_cas(
    db: Session,
    snap: ChunkVersionSnapshot,
    *,
    digests: list[str],
    moment: datetime,
) -> bool:
    """Écrit les digests seulement si la version fichier n'a pas changé (comparaison en base)."""
    if len(digests) != expected_chunk_count(snap.size):
        raise ValueError("chunk digest count mismatch")
    payload = json.dumps(digests, separators=(",", ":"))
    result = db.execute(
        update(HiveFileFingerprint)
        .where(
            HiveFileFingerprint.media_item_id == snap.media_item_id,
            HiveFileFingerprint.sha256_hex == snap.sha256_hex,
            HiveFileFingerprint.size == snap.size,
            HiveFileFingerprint.mtime_ns == snap.mtime_ns,
        )
        .values(
            chunk_digests_json=payload,
            chunk_digest_offset=snap.size,
            chunk_digest_partial_json=None,
            chunk_digest_claimed_at=None,
            chunk_digests_sha256_hex=snap.sha256_hex,
            updated_at=moment,
        )
    )
    return int(result.rowcount or 0) == 1


def persist_chunk_digests_row(db: Session, row: HiveFileFingerprint, *, digests: list[str]) -> None:
    size = int(row.size or 0)
    sha = (row.sha256_hex or "").strip()
    if not sha:
        raise ValueError("chunk digest persist without file sha256")
    if len(digests) != expected_chunk_count(size):
        raise ValueError("chunk digest count mismatch")
    row.chunk_digests_json = json.dumps(digests, separators=(",", ":"))
    row.chunk_digest_offset = size
    row.chunk_digest_partial_json = None
    row.chunk_digest_claimed_at = None
    row.chunk_digests_sha256_hex = sha
    db.flush()


def chunk_digests_header_from_row(row: HiveFileFingerprint) -> str:
    digests = chunk_digests_row_valid(row)
    if digests is None:
        raise ChunkDigestsNotReady()
    from mediatracker.hive.chunk_digest import chunk_digests_header_value

    return chunk_digests_header_value(digests)


def load_chunk_digests_for_media(db: Session, media_item_id: int) -> list[str]:
    row = db.get(HiveFileFingerprint, media_item_id)
    digests = chunk_digests_row_valid(row)
    if digests is None:
        raise ChunkDigestsNotReady()
    return digests


def fingerprint_needs_chunk_backfill(row: HiveFileFingerprint | None) -> bool:
    if not HIVE_CHUNK_DIGESTS_REQUIRED:
        return False
    if row is None:
        return False
    if not (row.sha256_hex or "").strip():
        return False
    return chunk_digests_row_valid(row) is None


def _backfill_scan_base():
    return (
        HiveFileFingerprint.sha256_hex.is_not(None),
        HiveFileFingerprint.chunk_digest_claimed_at.is_(None),
    )


def _expected_chunk_count_sql():
    return (HiveFileFingerprint.size + TRANSFER_CHUNK_SIZE - 1) // TRANSFER_CHUNK_SIZE


def _json_digest_count_sql():
    """Longueur du tableau JSON si valide, sinon -1 (json_array_length évité sur JSON invalide)."""
    raw = HiveFileFingerprint.chunk_digests_json
    return case(
        (func.json_valid(raw) == 1, func.json_array_length(raw)),
        else_=-1,
    )


def _complete_row_json_suspect():
    """Offset = taille et SHA lié cohérent, mais JSON absent, invalide ou mauvais nombre de morceaux."""
    bound_ok = or_(
        HiveFileFingerprint.chunk_digests_sha256_hex.is_(None),
        func.lower(HiveFileFingerprint.chunk_digests_sha256_hex) == func.lower(HiveFileFingerprint.sha256_hex),
    )
    expected = _expected_chunk_count_sql()
    digest_count = _json_digest_count_sql()
    return and_(
        HiveFileFingerprint.chunk_digests_json.is_not(None),
        HiveFileFingerprint.chunk_digest_offset == HiveFileFingerprint.size,
        bound_ok,
        or_(
            func.json_valid(HiveFileFingerprint.chunk_digests_json) != 1,
            digest_count != expected,
        ),
    )


def _backfill_candidate_sql_clause():
    """Présélection SQL : pas de ligne déjà valide ; JSON corrompu détecté via JSON1 (SQLite)."""
    bound_mismatch = and_(
        HiveFileFingerprint.chunk_digests_sha256_hex.is_not(None),
        func.lower(HiveFileFingerprint.chunk_digests_sha256_hex) != func.lower(HiveFileFingerprint.sha256_hex),
    )
    return or_(
        HiveFileFingerprint.chunk_digests_json.is_(None),
        HiveFileFingerprint.chunk_digest_offset != HiveFileFingerprint.size,
        HiveFileFingerprint.chunk_digests_sha256_hex.is_(None),
        bound_mismatch,
        _complete_row_json_suspect(),
    )


def _iter_backfill_scan_rows(db: Session, *, batch_size: int = 64, offset: int = 0):
    return db.scalars(
        select(HiveFileFingerprint)
        .join(HiveShareIntent, HiveShareIntent.media_item_id == HiveFileFingerprint.media_item_id)
        .where(*_backfill_scan_base(), _backfill_candidate_sql_clause())
        .order_by(HiveFileFingerprint.updated_at.asc())
        .offset(offset)
        .limit(batch_size)
    ).all()


def count_pending_chunk_backfill(db: Session) -> int:
    if not HIVE_CHUNK_DIGESTS_REQUIRED:
        return 0
    from mediatracker.hive.identity import hive_enabled_in_db

    if not hive_enabled_in_db(db):
        return 0
    n = 0
    offset = 0
    batch_size = 128
    while True:
        rows = _iter_backfill_scan_rows(db, batch_size=batch_size, offset=offset)
        if not rows:
            break
        offset += len(rows)
        for row in rows:
            if not item_wants_hive_share(db, int(row.media_item_id)):
                continue
            if fingerprint_needs_chunk_backfill(row):
                n += 1
        if len(rows) < batch_size:
            break
    return n


def list_backfill_candidate_ids_for_items(
    db: Session,
    media_item_ids: list[int],
    *,
    now: datetime | None = None,
) -> list[int]:
    """Candidats morceaux limités à une liste d'épisodes (pas de plafond global)."""
    if not HIVE_CHUNK_DIGESTS_REQUIRED:
        return []
    if not media_item_ids:
        return []
    moment = now or datetime.now(timezone.utc)
    uniq = sorted({int(mid) for mid in media_item_ids})
    rows = list(
        db.scalars(
            select(HiveFileFingerprint)
            .join(HiveShareIntent, HiveShareIntent.media_item_id == HiveFileFingerprint.media_item_id)
            .where(
                HiveFileFingerprint.media_item_id.in_(uniq),
                *_backfill_scan_base(),
                _backfill_candidate_sql_clause(),
            )
        ).all()
    )
    ids: list[int] = []
    for row in rows:
        mid = int(row.media_item_id)
        if not fingerprint_needs_chunk_backfill(row):
            continue
        if not item_wants_hive_share(db, mid):
            row.updated_at = moment
            db.flush()
            continue
        ids.append(mid)
    return ids


def list_backfill_candidate_ids(db: Session, *, limit: int = 32, now: datetime | None = None) -> list[int]:
    """Identifiants éligibles ; pagination pour ne pas masquer une ligne à réparer."""
    if not HIVE_CHUNK_DIGESTS_REQUIRED:
        return []
    moment = now or datetime.now(timezone.utc)
    ids: list[int] = []
    offset = 0
    batch_size = 64
    while len(ids) < limit:
        rows = _iter_backfill_scan_rows(db, batch_size=batch_size, offset=offset)
        if not rows:
            break
        offset += len(rows)
        for row in rows:
            mid = int(row.media_item_id)
            if not fingerprint_needs_chunk_backfill(row):
                continue
            if not item_wants_hive_share(db, mid):
                row.updated_at = moment
                db.flush()
                continue
            ids.append(mid)
            if len(ids) >= limit:
                break
        if len(rows) < batch_size:
            break
    return ids


def persist_chunk_partial_cas(
    db: Session,
    snap: ChunkVersionSnapshot,
    *,
    offset: int,
    partial_json: str,
    moment: datetime,
) -> bool:
    result = db.execute(
        update(HiveFileFingerprint)
        .where(
            HiveFileFingerprint.media_item_id == snap.media_item_id,
            HiveFileFingerprint.sha256_hex == snap.sha256_hex,
            HiveFileFingerprint.size == snap.size,
            HiveFileFingerprint.mtime_ns == snap.mtime_ns,
        )
        .values(
            chunk_digest_offset=offset,
            chunk_digest_partial_json=partial_json,
            updated_at=moment,
        )
    )
    return int(result.rowcount or 0) == 1


def chunk_digest_claim_is_stale(claimed_at: datetime | None, *, now: datetime) -> bool:
    if claimed_at is None:
        return False
    claimed = claimed_at
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=timezone.utc)
    return claimed < (now - HIVE_CLAIM_STALE)


def recover_stale_chunk_claim_for_item(db: Session, media_item_id: int, *, now: datetime) -> bool:
    """Libère un claim morceau périmé pour un seul épisode (ne touche pas aux claims actifs)."""
    row = db.get(HiveFileFingerprint, int(media_item_id))
    if row is None or row.chunk_digest_claimed_at is None:
        return False
    if not chunk_digest_claim_is_stale(row.chunk_digest_claimed_at, now=now):
        return False
    row.chunk_digest_claimed_at = None
    db.flush()
    return True


def recover_stale_chunk_digest_claims(db: Session, *, now: datetime) -> int:
    changed = 0
    rows = list(
        db.scalars(
            select(HiveFileFingerprint).where(HiveFileFingerprint.chunk_digest_claimed_at.is_not(None))
        ).all()
    )
    for row in rows:
        if chunk_digest_claim_is_stale(row.chunk_digest_claimed_at, now=now):
            row.chunk_digest_claimed_at = None
            changed += 1
    if changed:
        db.flush()
    return changed


def ingest_chunk_header_cache(value: str | None) -> list[str] | None:
    return parse_chunk_digests_header(value)


def is_chunks_pending_http(status: int, headers: dict[str, str]) -> bool:
    if status != 503:
        return False
    for key, val in headers.items():
        if key.lower() == HIVE_CHUNKS_PENDING_HEADER.lower():
            return val.strip() == "1"
    return False
