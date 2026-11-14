"""Lecture incrémentale SHA-256 : pas de relecture du préfixe entre les budgets."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from mediatracker.hive.chunk_digest_stream import TransferChunkDigestAccumulator
from mediatracker.hive.fingerprint import CHUNK_SIZE, MAX_CHUNKS_PER_TICK

READ_BUDGET_BYTES = MAX_CHUNKS_PER_TICK * CHUNK_SIZE


@dataclass
class StreamingSha256Session:
    """État de hachage conservé en mémoire pendant la vie du processus empreintes."""

    path: Path
    size: int
    offset: int
    hasher: hashlib._Hash
    bytes_read_last_budget: int = 0
    transfer_chunks: TransferChunkDigestAccumulator | None = None

    @classmethod
    def open_fresh(cls, path: Path, *, size: int, with_transfer_chunks: bool = False) -> StreamingSha256Session:
        chunks = TransferChunkDigestAccumulator(file_size=size) if with_transfer_chunks else None
        return cls(
            path=path,
            size=size,
            offset=0,
            hasher=hashlib.sha256(),
            transfer_chunks=chunks,
        )

    @classmethod
    def resume_from_disk(
        cls,
        path: Path,
        *,
        size: int,
        partial: int,
        expected_prefix_hex: str | None,
        with_transfer_chunks: bool = False,
    ) -> StreamingSha256Session | None:
        """Relecture ponctuelle du préfixe (redémarrage du processus ou reprise de claim)."""
        if partial <= 0:
            return cls.open_fresh(path, size=size, with_transfer_chunks=with_transfer_chunks)
        h = hashlib.sha256()
        read = 0
        try:
            with path.open("rb") as handle:
                while read < partial:
                    chunk = handle.read(min(CHUNK_SIZE, partial - read))
                    if not chunk:
                        return None
                    h.update(chunk)
                    read += len(chunk)
        except OSError:
            return None
        if read != partial:
            return None
        if expected_prefix_hex is not None and h.hexdigest() != expected_prefix_hex:
            return None
        chunks = TransferChunkDigestAccumulator(file_size=size) if with_transfer_chunks else None
        if chunks is not None and read > 0:
            with path.open("rb") as handle:
                handle.seek(0)
                fed = 0
                while fed < read:
                    block = handle.read(min(CHUNK_SIZE, read - fed))
                    if not block:
                        break
                    chunks.feed(block)
                    fed += len(block)
        return cls(path=path, size=size, offset=read, hasher=h, transfer_chunks=chunks)

    def read_budget(self, budget: int | None = None) -> int:
        """Lit au plus ``budget`` octets nouveaux depuis ``offset`` ; ne relit pas le préfixe."""
        self.bytes_read_last_budget = 0
        if self.offset >= self.size:
            return self.offset
        cap = budget if budget is not None else READ_BUDGET_BYTES
        remaining_budget = cap
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                while self.offset < self.size and remaining_budget > 0:
                    to_read = min(CHUNK_SIZE, self.size - self.offset, remaining_budget)
                    chunk = handle.read(to_read)
                    if not chunk:
                        break
                    self.hasher.update(chunk)
                    if self.transfer_chunks is not None:
                        self.transfer_chunks.feed(chunk)
                    n = len(chunk)
                    self.offset += n
                    remaining_budget -= n
                    self.bytes_read_last_budget += n
        except OSError:
            return self.offset
        return self.offset
