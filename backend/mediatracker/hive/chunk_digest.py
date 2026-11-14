"""Empreintes SHA-256 par morceaux de 16 Mio (transfert Hive T5)."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

TRANSFER_CHUNK_SIZE = 16 * 1024 * 1024


def chunk_digests_for_file(path: Path, *, size: int | None = None) -> list[str]:
    """Calcule la liste d'empreintes hex SHA-256, un morceau par index."""
    real_size = size if size is not None else path.stat().st_size
    digests: list[str] = []
    with path.open("rb") as f:
        offset = 0
        while offset < real_size:
            length = min(TRANSFER_CHUNK_SIZE, real_size - offset)
            h = hashlib.sha256()
            read = 0
            while read < length:
                block = f.read(min(65536, length - read))
                if not block:
                    break
                h.update(block)
                read += len(block)
            digests.append(h.hexdigest())
            offset += length
    return digests


def chunk_digests_header_value(digests: list[str]) -> str:
    raw = json.dumps(digests, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_chunk_digests_header(value: str | None) -> list[str] | None:
    if not value or not value.strip():
        return None
    pad = "=" * ((4 - len(value.strip()) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode((value.strip() + pad).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        return None
    return data


def chunk_index_for_offset(offset: int) -> int:
    if offset < 0:
        return 0
    return offset // TRANSFER_CHUNK_SIZE


def chunk_byte_range(chunk_index: int, file_size: int) -> tuple[int, int]:
    start = chunk_index * TRANSFER_CHUNK_SIZE
    end = min(file_size, start + TRANSFER_CHUNK_SIZE) - 1
    return start, max(start, end)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(65536)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
