"""Persistance des nonces vus (10 minutes)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.db.models import HivePeerNonce
from mediatracker.hive.peer_auth import PeerAuthDenied

_RETENTION = timedelta(minutes=10)


def _purge_old(db: Session, *, now: datetime) -> None:
    cutoff = now - _RETENTION
    db.execute(delete(HivePeerNonce).where(HivePeerNonce.seen_at < cutoff))


def consume_nonce(db: Session, *, hive_id: str, nonce: bytes, now: datetime) -> None:
    _purge_old(db, now=now)
    exists = db.scalar(
        select(HivePeerNonce.id).where(HivePeerNonce.hive_id == hive_id, HivePeerNonce.nonce == nonce).limit(1)
    )
    if exists is not None:
        raise PeerAuthDenied("nonce_replay")
    db.add(HivePeerNonce(hive_id=hive_id, nonce=nonce, seen_at=now))
    db.flush()
