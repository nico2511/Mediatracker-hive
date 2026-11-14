"""Réveil du processus dédié empreintes (FIFO séparée du worker principal)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.worker_wake import REASON_HIVE_FINGERPRINT, wake_counts
from mediatracker.db.models import Setting

logger = logging.getLogger(__name__)

WAKE_COUNTS_KEY = "worker.wake_counts"
WAKE_SEQ_KEY = "worker.wake_seq"
FINGERPRINT_FIFO_NAME = "fingerprint.wake.fifo"


def fingerprint_fifo_path(settings: Settings | None = None, config_dir: Path | None = None) -> Path:
    if config_dir is not None:
        return config_dir / FINGERPRINT_FIFO_NAME
    from mediatracker.config import get_settings

    base = Path((settings or get_settings()).config_dir)
    return base / FINGERPRINT_FIFO_NAME


def poke_fingerprint_fifo_only(settings: Settings) -> None:
    """Signal FIFO sans écriture SQLite (chemin chaud 503 GET /hive/files)."""
    _poke_fifo(fingerprint_fifo_path(settings))


def wake_fingerprint_daemon(db: Session, settings: Settings, *, commit: bool = True) -> None:
    """Compteur de réveil (métriques) + poke FIFO empreintes."""
    moment = datetime.now(timezone.utc)
    counts = wake_counts(db)
    counts[REASON_HIVE_FINGERPRINT] = counts.get(REASON_HIVE_FINGERPRINT, 0) + 1
    _put(db, WAKE_COUNTS_KEY, json.dumps(counts, sort_keys=True), moment)
    seq = _int_setting(db, WAKE_SEQ_KEY) + 1
    _put(db, WAKE_SEQ_KEY, str(seq), moment)
    if commit:
        db.commit()
    else:
        db.flush()
    _poke_fifo(fingerprint_fifo_path(settings))


def _poke_fifo(path: Path) -> None:
    try:
        if not path.exists():
            return
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, b"f")
        finally:
            os.close(fd)
    except OSError:
        return


def _put(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def _int_setting(db: Session, key: str) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return 0
    try:
        return int(row.value)
    except ValueError:
        return 0
