"""Service GET /hive/files/{file_id} (Range, empreintes, garde-fous T5)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveEntry, HiveFileFingerprint
from mediatracker.hive.catalog import member_ruche_ids_for_instance
from mediatracker.hive.chunk_digest import TRANSFER_CHUNK_SIZE, chunk_digests_header_value, parse_chunk_digests_header
from mediatracker.hive.chunk_digest_store import load_chunk_digests_for_media
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.transfer_policy import (
    TransferQuotaExceeded,
    TransferWindowClosed,
    check_inbound_fetch,
    local_can_send,
    record_inbound_bytes,
)

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)", re.I)
HIVE_FILE_SEND_BLOCK = 1024 * 1024


@dataclass(frozen=True)
class LocalFileBinding:
    """Métadonnées résolues en SQLite uniquement (pas de lecture fichier lourde)."""

    path: Path
    size: int
    mtime_ns: int
    sha256_hex: str
    ruche_id: bytes
    media_item_id: int


@dataclass(frozen=True)
class ServedFile:
    path: Path
    size: int
    sha256_hex: str
    chunk_digests: list[str]
    ruche_id: bytes
    media_item_id: int


@dataclass(frozen=True)
class FileServePlan:
    binding: LocalFileBinding
    byte_range: ByteRange
    status: int
    base_headers: dict[str, str]
    chunk_digests: list[str]


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int  # inclusive

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _parse_range(header: str | None, size: int) -> ByteRange | None:
    if not header or not header.strip():
        return ByteRange(0, size - 1) if size > 0 else ByteRange(0, 0)
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    start = int(m.group(1))
    end_s = m.group(2)
    end = int(end_s) if end_s else size - 1
    if start < 0 or end < start or start >= size:
        return None
    end = min(end, size - 1)
    return ByteRange(start, end)


def resolve_local_file(
    db: Session,
    settings,
    *,
    ruche_id: bytes,
    file_id: bytes,
) -> LocalFileBinding | None:
    if not local_can_send(db):
        return None
    pk = load_private_key(settings)
    if pk is None:
        return None
    origin = hive_public_id_from_public_key(pk.public_key())
    if ruche_id not in member_ruche_ids_for_instance(db, origin):
        return None
    row = db.scalar(
        select(HiveEntry)
        .where(
            HiveEntry.ruche_id == ruche_id,
            HiveEntry.file_id == file_id,
            HiveEntry.origin_instance_id == origin,
            HiveEntry.etat == "disponible",
        )
        .order_by(HiveEntry.seq.desc())
        .limit(1)
    )
    if row is None or row.media_item_id is None or not row.sha256_hex:
        return None
    fp = db.get(HiveFileFingerprint, row.media_item_id)
    if fp is None or not fp.sha256_hex or fp.sha256_hex != row.sha256_hex:
        return None
    path = Path(fp.path)
    if not path.is_file():
        return None
    st = path.stat()
    size = st.st_size
    if size != fp.size:
        return None
    mtime_ns = getattr(st, "st_mtime_ns", None)
    if mtime_ns is None:
        mtime_ns = int(st.st_mtime * 1_000_000_000)
    if fp.mtime_ns is not None and int(fp.mtime_ns) != int(mtime_ns):
        return None
    return LocalFileBinding(
        path=path,
        size=size,
        mtime_ns=int(mtime_ns),
        sha256_hex=row.sha256_hex,
        ruche_id=ruche_id,
        media_item_id=row.media_item_id,
    )


def finalize_response_headers(plan: FileServePlan, chunk_digests: list[str]) -> dict[str, str]:
    headers = dict(plan.base_headers)
    if chunk_digests:
        headers["Hive-Chunk-Sha256"] = chunk_digests_header_value(chunk_digests)
    headers["Content-Length"] = str(plan.byte_range.length)
    if plan.status == 206:
        headers["Content-Range"] = content_range_header(plan.byte_range, plan.binding.size)
    return headers


def prepare_file_response(
    db: Session,
    *,
    binding: LocalFileBinding,
    requester_id: str,
    range_header: str | None,
    now: datetime,
    require_chunk_digests: bool = True,
    record_transfer_bytes: bool = True,
) -> FileServePlan | None:
    """Contrôles SQLite + quota. Les empreintes par morceau se calculent hors session."""
    br = _parse_range(range_header, binding.size)
    if br is None:
        return None
    try:
        check_inbound_fetch(
            db,
            ruche_id=binding.ruche_id,
            requester_id=requester_id,
            content_length=br.length,
            now=now,
        )
    except (TransferQuotaExceeded, TransferWindowClosed):
        raise
    headers = {
        "Hive-Content-Sha256": binding.sha256_hex,
        "Hive-Content-Length": str(binding.size),
        "Accept-Ranges": "bytes",
    }
    status = 200 if br.start == 0 and br.end == binding.size - 1 else 206
    chunk_digests: list[str] = []
    if require_chunk_digests:
        chunk_digests = load_chunk_digests_for_media(db, binding.media_item_id)
    return FileServePlan(
        binding=binding,
        byte_range=br,
        status=status,
        base_headers=headers,
        chunk_digests=chunk_digests,
    )


def read_exact_range(path: Path, byte_range: ByteRange) -> bytes:
    """Lecture intégrale d'une plage (petits blocs de test uniquement ; pas pour GET HTTP)."""
    if byte_range.length > HIVE_FILE_SEND_BLOCK:
        raise ValueError("Plage trop grande pour read_exact_range ; utiliser iter_file_byte_range.")
    return b"".join(iter_file_byte_range(path, byte_range))


def iter_file_byte_range(
    path: Path,
    byte_range: ByteRange,
    *,
    block_size: int = HIVE_FILE_SEND_BLOCK,
) -> Iterator[bytes]:
    """Générateur sync : lit la plage par morceaux (fermeture du fichier à la fin ou à la déconnexion)."""
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    with path.open("rb") as handle:
        handle.seek(byte_range.start)
        remaining = byte_range.length
        while remaining > 0:
            chunk = handle.read(min(block_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def content_range_header(byte_range: ByteRange, total: int) -> str:
    return f"bytes {byte_range.start}-{byte_range.end}/{total}"


def persist_inbound_quota_bytes(
    app,
    *,
    ruche_id: bytes,
    requester_id: str,
    nbytes: int,
    now: datetime,
) -> None:
    """Compteur quota après envoi réel (requêtes Range et reprises)."""
    if nbytes <= 0:
        return
    session = app.state.session_factory()
    try:
        record_inbound_bytes(
            session,
            ruche_id=ruche_id,
            requester_id=requester_id,
            nbytes=nbytes,
            now=now,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def exposed_chunk_size() -> int:
    return TRANSFER_CHUNK_SIZE


def cache_chunk_digests_from_header(value: str | None) -> list[str] | None:
    return parse_chunk_digests_header(value)
