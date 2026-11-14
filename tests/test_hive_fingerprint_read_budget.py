"""Budget de lecture disque par passage worker pour les empreintes Hive."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from mediatracker.hive.fingerprint import (
    CHUNK_SIZE,
    MAX_CHUNKS_PER_TICK,
    _hash_file_budget,
)

_NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
_MIB = 1024 * 1024


def test_fingerprint_worker_read_budget_is_32_mib():
    assert CHUNK_SIZE == _MIB
    assert MAX_CHUNKS_PER_TICK == 32
    assert MAX_CHUNKS_PER_TICK * CHUNK_SIZE == 32 * _MIB


def _hash_via_budgeted_reads(path: Path, size: int) -> str:
    partial = 0
    digest = hashlib.sha256()
    while partial < size:
        read_upto, h = _hash_file_budget(path, partial_start=partial, size=size, moment_unused=_NOW)
        assert read_upto > partial
        partial = read_upto
        digest = h
    return digest.hexdigest()


def test_fingerprint_budgeted_reads_match_hashlib(tmp_path):
    sizes = (
        0,
        1,
        CHUNK_SIZE - 1,
        CHUNK_SIZE,
        32 * _MIB - 1,
        32 * _MIB,
        32 * _MIB + 1,
        40 * _MIB,
        100 * _MIB + 17,
    )
    for size in sizes:
        path = tmp_path / f"fictive-{size}.bin"
        payload = bytes([(n * 31 + 7) % 256 for n in range(size)])
        path.write_bytes(payload)
        expected = hashlib.sha256(payload).hexdigest()
        if size == 0:
            _, h = _hash_file_budget(path, partial_start=0, size=0, moment_unused=_NOW)
            assert h.hexdigest() == expected
        else:
            assert _hash_via_budgeted_reads(path, size) == expected


def test_single_tick_reads_at_most_budget_new_bytes(tmp_path):
    size = 50 * _MIB
    path = tmp_path / "large-fictive.bin"
    path.write_bytes(b"\x00" * size)
    read_upto, _ = _hash_file_budget(path, partial_start=0, size=size, moment_unused=_NOW)
    assert read_upto == 32 * _MIB
