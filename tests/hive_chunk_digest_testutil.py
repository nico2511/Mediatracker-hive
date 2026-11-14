"""Utilitaires tests : remplir les digests morceaux sans passer par GET /hive/files."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveFileFingerprint
from mediatracker.hive.chunk_digest import chunk_digests_for_file
from mediatracker.hive.chunk_digest_store import persist_chunk_digests_row


def persist_chunk_digests_for_test_file(db: Session, fp: HiveFileFingerprint) -> None:
    """Calcule hors handler (tests / seed) et écrit en base."""
    from pathlib import Path

    path = Path(fp.path)
    digests = chunk_digests_for_file(path, size=int(fp.size))
    persist_chunk_digests_row(db, fp, digests=digests)


def persist_chunk_digests_corrupt(db: Session, fp: HiveFileFingerprint, *, bad_json: str) -> None:
    fp.chunk_digests_json = bad_json
    fp.chunk_digest_offset = 0
    db.flush()


def run_chunk_backfill_until_ready(
    db: Session, settings: Settings, media_item_id: int, *, max_ticks: int = 500
) -> HiveFileFingerprint:
    from mediatracker.hive.chunk_digest_backfill import chunk_digest_backfill_tick

    for _ in range(max_ticks):
        chunk_digest_backfill_tick(db, settings)
        fp = db.get(HiveFileFingerprint, media_item_id)
        assert fp is not None
        if fp.chunk_digests_json:
            data = json.loads(fp.chunk_digests_json)
            assert isinstance(data, list) and data
            return fp
    raise AssertionError("chunk digest backfill did not finish within tick budget")
