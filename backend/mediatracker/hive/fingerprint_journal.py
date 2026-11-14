"""Journal Hive : début, fin et erreur de calcul d'empreinte (dédoublonnage)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.db.models import MediaItem
from mediatracker.events import record_event

_TYPE_STARTED = "hive.fingerprint.started"
_TYPE_DONE = "hive.fingerprint.done"
_TYPE_ERROR = "hive.fingerprint.error"


def record_fingerprint_started(
    db: Session,
    item: MediaItem,
    *,
    size: int,
    mtime_ns: int,
    when: datetime,
) -> None:
    assert item.id is not None
    record_event(
        db,
        _TYPE_STARTED,
        level="info",
        media_id=item.media_id,
        item_id=item.id,
        payload={"message": "Calcul d'empreinte SHA-256 démarré.", "size": size, "mtime_ns": mtime_ns},
        dedupe_key=f"hive-fp-start:{item.id}:{size}:{mtime_ns}",
        when=when,
    )


def record_fingerprint_done(
    db: Session,
    item: MediaItem,
    *,
    sha256_hex: str,
    size: int,
    when: datetime,
) -> None:
    assert item.id is not None
    record_event(
        db,
        _TYPE_DONE,
        level="info",
        media_id=item.media_id,
        item_id=item.id,
        payload={"message": "Empreinte SHA-256 enregistrée.", "sha256": sha256_hex, "size": size},
        dedupe_key=f"hive-fp-done:{item.id}:{sha256_hex}",
        when=when,
    )


def record_fingerprint_error(
    db: Session,
    item: MediaItem,
    *,
    message: str,
    size: int,
    mtime_ns: int,
    when: datetime,
) -> None:
    assert item.id is not None
    record_event(
        db,
        _TYPE_ERROR,
        level="warning",
        media_id=item.media_id,
        item_id=item.id,
        payload={"message": message, "size": size, "mtime_ns": mtime_ns},
        dedupe_key=f"hive-fp-error:{item.id}:{size}:{mtime_ns}",
        when=when,
    )
