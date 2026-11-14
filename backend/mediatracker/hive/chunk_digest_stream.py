"""Accumulateur SHA-256 par morceau 16 Mio pendant une lecture séquentielle."""

from __future__ import annotations

import hashlib

from mediatracker.hive.chunk_digest import TRANSFER_CHUNK_SIZE


class TransferChunkDigestAccumulator:
    """Mis à jour sur les mêmes octets que le SHA-256 fichier."""

    def __init__(self, *, file_size: int) -> None:
        self._file_size = file_size
        self._completed: list[str] = []
        self._current = hashlib.sha256()
        self._current_len = 0
        self._file_offset = 0

    @property
    def completed(self) -> list[str]:
        return list(self._completed)

    @property
    def file_offset(self) -> int:
        return self._file_offset

    def feed(self, data: bytes) -> None:
        view = memoryview(data)
        pos = 0
        while pos < len(view) and self._file_offset < self._file_size:
            room = TRANSFER_CHUNK_SIZE - self._current_len
            take = min(room, len(view) - pos, self._file_size - self._file_offset)
            if take <= 0:
                break
            block = view[pos : pos + take]
            self._current.update(block)
            self._current_len += take
            self._file_offset += take
            pos += take
            at_eof = self._file_offset >= self._file_size
            if self._current_len >= TRANSFER_CHUNK_SIZE or at_eof:
                self._completed.append(self._current.hexdigest())
                self._current = hashlib.sha256()
                self._current_len = 0
            if at_eof:
                break

    def restore(self, *, completed: list[str], file_offset: int) -> None:
        if not self.try_restore(completed=completed, file_offset=file_offset):
            self._completed = []
            self._file_offset = 0
            self._current = hashlib.sha256()
            self._current_len = 0

    def try_restore(self, *, completed: list[str], file_offset: int) -> bool:
        """Reprise uniquement sur frontière de morceau (état partiel cohérent)."""
        expected = len(completed) * TRANSFER_CHUNK_SIZE
        if file_offset != expected:
            return False
        if file_offset > self._file_size:
            return False
        self._completed = list(completed)
        self._file_offset = file_offset
        self._current = hashlib.sha256()
        self._current_len = 0
        return True

    def is_complete(self) -> bool:
        if self._file_offset < self._file_size:
            return False
        return len(self._completed) == expected_chunks(self._file_size)


def expected_chunks(file_size: int) -> int:
    if file_size <= 0:
        return 0
    return (file_size + TRANSFER_CHUNK_SIZE - 1) // TRANSFER_CHUNK_SIZE
