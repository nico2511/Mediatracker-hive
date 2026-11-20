"""Signaux Hive possede/cherche : publication locale, gossip, retrait."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.file_status import FileStatusIndex
from mediatracker.db.models import HiveSignal, HiveSignalVersions, Media, MediaItem
from mediatracker.hive.access import is_active_member
from mediatracker.hive.catalog_epoch import local_catalog_epoch, payload_catalog_epoch
from mediatracker.hive.crypto_id import verify_document_signature
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.peer_gossip import _origin_public_key

SIGNAL_DOCUMENT_KIND = "signal"
SIGNAL_DOCUMENT_VERSION = 2


def cle_hash(cle: dict) -> str:
    text = json.dumps(cle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_cle(text: str | None) -> dict:
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def build_signal_payload(
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    seq: int,
    signal: str,
    cle: dict,
    mini_fiche: dict | None = None,
    catalog_epoch: int = 0,
) -> dict:
    payload: dict = {
        "v": SIGNAL_DOCUMENT_VERSION,
        "kind": SIGNAL_DOCUMENT_KIND,
        "ruche_id": ruche_id.hex(),
        "origine": origin_instance_id,
        "seq": seq,
        "signal": signal,
        "cle": cle,
        "catalog_epoch": int(catalog_epoch),
    }
    if mini_fiche:
        payload["mini_fiche"] = mini_fiche
    return payload


def next_signal_seq(db: Session, ruche_id: bytes, origin_instance_id: str) -> int:
    m = db.scalar(
        select(func.max(HiveSignal.seq)).where(
            HiveSignal.ruche_id == ruche_id,
            HiveSignal.origin_instance_id == origin_instance_id,
        )
    )
    seq = 1 if m is None else int(m) + 1
    row = db.get(HiveSignalVersions, (ruche_id, origin_instance_id))
    if row is None:
        db.add(HiveSignalVersions(ruche_id=ruche_id, origin_instance_id=origin_instance_id, seq=seq))
    else:
        row.seq = seq
    return seq


def _compact_signal_rows(
    db: Session, *, ruche_id: bytes, origin_id: str, cle_key: str, keep_id: int | None = None
) -> None:
    q = delete(HiveSignal).where(
        HiveSignal.ruche_id == ruche_id,
        HiveSignal.origin_instance_id == origin_id,
        HiveSignal.cle_hash == cle_key,
    )
    if keep_id is not None:
        q = q.where(HiveSignal.id != keep_id)
    db.execute(q)


def _latest_signal_row(
    db: Session, *, ruche_id: bytes, origin_id: str, cle: dict
) -> HiveSignal | None:
    target = cle_hash(cle)
    return db.scalar(
        select(HiveSignal)
        .where(
            HiveSignal.ruche_id == ruche_id,
            HiveSignal.origin_instance_id == origin_id,
            HiveSignal.cle_hash == target,
        )
        .limit(1)
    )


def _publish_signal(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    origin_id: str,
    private_key,
    signal: str,
    cle: dict,
    media_item_id: int | None,
    now: datetime,
    mini_fiche: dict | None = None,
) -> None:
    latest = _latest_signal_row(db, ruche_id=ruche_id, origin_id=origin_id, cle=cle)
    if latest is not None and latest.signal == signal:
        return
    seq = next_signal_seq(db, ruche_id, origin_id)
    from mediatracker.hive.signal_mini_fiche import sanitize_mini_fiche

    safe_mini = sanitize_mini_fiche(mini_fiche) if mini_fiche else None
    epoch = local_catalog_epoch(db, ruche_id)
    payload = build_signal_payload(
        ruche_id=ruche_id,
        origin_instance_id=origin_id,
        seq=seq,
        signal=signal,
        cle=cle,
        mini_fiche=safe_mini,
        catalog_epoch=epoch,
    )
    _, _, _, sign_fn = _catalog_helpers()
    sig = sign_fn(private_key, payload)
    cle_key = cle_hash(cle)
    _compact_signal_rows(db, ruche_id=ruche_id, origin_id=origin_id, cle_key=cle_key)
    row = HiveSignal(
        ruche_id=ruche_id,
        origin_instance_id=origin_id,
        seq=seq,
        created_at=now,
        signal=signal,
        cle_hash=cle_key,
        cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        signature_b64=sig,
        media_item_id=media_item_id,
        mini_fiche_json=(
            json.dumps(safe_mini, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if safe_mini
            else None
        ),
    )
    db.add(row)
    db.flush()
    _compact_signal_rows(db, ruche_id=ruche_id, origin_id=origin_id, cle_key=cle_key, keep_id=row.id)
    from mediatracker.hive.gossip_burst import mark_local_catalog_change

    mark_local_catalog_change(db, ruche_id=ruche_id, now=now)


def item_file_present(db: Session, item: MediaItem) -> bool:
    if item.id is None:
        return False
    idx = FileStatusIndex.load(db, [item])
    if idx.for_item(item) == "present":
        return True
    if item.downloaded and item.file_path:
        return Path(item.file_path).is_file()
    return False


def _catalog_helpers():
    from mediatracker.hive.catalog import (
        _cle_document,
        _origin_for_signing,
        member_ruche_ids_for_instance,
        sign_payload,
    )

    return _cle_document, _origin_for_signing, member_ruche_ids_for_instance, sign_payload


def retract_possede_for_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> None:
    if not hive_enabled_in_db(db) or item.id is None:
        return
    cle_doc, origin_fn, ruches_fn, _ = _catalog_helpers()
    private_key, origin_id = origin_fn(settings)
    if private_key is None or origin_id is None:
        return
    cle = cle_doc(media, item)
    for rid in ruches_fn(db, origin_id):
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin_id, cle=cle)
        if latest is None or latest.signal != "possede":
            continue
        _publish_signal(
            db,
            settings,
            ruche_id=rid,
            origin_id=origin_id,
            private_key=private_key,
            signal="retire",
            cle=cle,
            media_item_id=item.id,
            now=now,
        )


def sync_possede_for_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> None:
    if not hive_enabled_in_db(db) or item.id is None:
        return
    cle_doc, origin_fn, ruches_fn, _ = _catalog_helpers()
    private_key, origin_id = origin_fn(settings)
    if private_key is None or origin_id is None:
        return
    cle = cle_doc(media, item)
    if not item_file_present(db, item):
        retract_possede_for_item(db, settings, media=media, item=item, now=now)
        return
    for rid in ruches_fn(db, origin_id):
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin_id, cle=cle)
        if latest is not None and latest.signal == "cherche":
            continue
        _publish_signal(
            db,
            settings,
            ruche_id=rid,
            origin_id=origin_id,
            private_key=private_key,
            signal="possede",
            cle=cle,
            media_item_id=item.id,
            now=now,
        )


def publish_cherche_for_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> bool:
    if not hive_enabled_in_db(db) or item.id is None or not media.followed:
        return False
    if item_file_present(db, item) or item.watched:
        return False
    cle_doc, origin_fn, ruches_fn, _ = _catalog_helpers()
    private_key, origin_id = origin_fn(settings)
    if private_key is None or origin_id is None:
        return False
    cle = cle_doc(media, item)
    from mediatracker.hive.signal_mini_fiche import build_cherche_mini_fiche

    mini_fiche = build_cherche_mini_fiche(media, item)
    for rid in ruches_fn(db, origin_id):
        _retract_superseded_cherche_slot(
            db,
            settings,
            ruche_id=rid,
            origin_id=origin_id,
            private_key=private_key,
            new_cle=cle,
            media_item_id=item.id,
            now=now,
        )
        _publish_signal(
            db,
            settings,
            ruche_id=rid,
            origin_id=origin_id,
            private_key=private_key,
            signal="cherche",
            cle=cle,
            media_item_id=item.id,
            now=now,
            mini_fiche=mini_fiche,
        )
    return True


def _retract_superseded_cherche_slot(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    origin_id: str,
    private_key,
    new_cle: dict,
    media_item_id: int | None,
    now: datetime,
) -> None:
    """Retire les cherche actifs sur le même créneau (œuvre/saison/épisode) mais autre version."""
    from mediatracker.hive.hive_catalogue import _stable_work_key

    target_wk = _stable_work_key(new_cle)
    target_hash = cle_hash(new_cle)
    rows = list(
        db.scalars(
            select(HiveSignal).where(
                HiveSignal.ruche_id == ruche_id,
                HiveSignal.origin_instance_id == origin_id,
                HiveSignal.signal == "cherche",
            )
        ).all()
    )
    for row in rows:
        old_cle = parse_cle(row.cle_json)
        if not old_cle or _stable_work_key(old_cle) != target_wk:
            continue
        if cle_hash(old_cle) == target_hash:
            continue
        _publish_signal(
            db,
            settings,
            ruche_id=ruche_id,
            origin_id=origin_id,
            private_key=private_key,
            signal="retire",
            cle=old_cle,
            media_item_id=media_item_id,
            now=now,
        )


def retract_cherche_for_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> None:
    if not hive_enabled_in_db(db) or item.id is None:
        return
    cle_doc, origin_fn, ruches_fn, _ = _catalog_helpers()
    private_key, origin_id = origin_fn(settings)
    if private_key is None or origin_id is None:
        return
    cle = cle_doc(media, item)
    for rid in ruches_fn(db, origin_id):
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin_id, cle=cle)
        if latest is None or latest.signal != "cherche":
            continue
        _publish_signal(
            db,
            settings,
            ruche_id=rid,
            origin_id=origin_id,
            private_key=private_key,
            signal="retire",
            cle=cle,
            media_item_id=item.id,
            now=now,
        )


def retract_cherche_for_media(db: Session, settings: Settings, *, media: Media, now: datetime) -> None:
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    for item in items:
        retract_cherche_for_item(db, settings, media=media, item=item, now=now)


def active_cherche_origins(
    db: Session, *, ruche_id: bytes, cle: dict, self_origin: str | None
) -> dict[str, int]:
    target = cle_hash(cle)
    rows = list(
        db.scalars(
            select(HiveSignal).where(HiveSignal.ruche_id == ruche_id, HiveSignal.cle_hash == target)
        ).all()
    )
    out: dict[str, int] = {}
    for row in rows:
        origin = row.origin_instance_id
        if not origin or (self_origin and origin == self_origin):
            continue
        if row.signal == "cherche":
            out[origin] = 1
    return out


def latest_cherche_rows_for_ruche(db: Session, *, ruche_id: bytes) -> list[HiveSignal]:
    return list(
        db.scalars(
            select(HiveSignal).where(HiveSignal.ruche_id == ruche_id, HiveSignal.signal == "cherche")
        ).all()
    )


def verify_remote_signal(db: Session, *, ruche_id: bytes, payload: dict, signature_b64: str) -> None:
    if payload.get("kind") != SIGNAL_DOCUMENT_KIND:
        raise ValueError("bad_kind")
    if int(payload.get("v") or 0) != SIGNAL_DOCUMENT_VERSION:
        raise ValueError("bad_version")
    rid = payload.get("ruche_id")
    if not isinstance(rid, str) or bytes.fromhex(rid) != ruche_id:
        raise ValueError("bad_ruche")
    origin = payload.get("origine")
    if not isinstance(origin, str):
        raise ValueError("bad_origin")
    if not is_active_member(db, ruche_id=ruche_id, instance_id=origin):
        raise ValueError("unknown_origin")
    signal = payload.get("signal")
    if signal not in {"possede", "cherche", "retire"}:
        raise ValueError("bad_signal")
    cle = payload.get("cle")
    if not isinstance(cle, dict):
        raise ValueError("bad_cle")
    pub = _origin_public_key(db, ruche_id=ruche_id, origin_id=origin)
    if pub is None:
        raise ValueError("no_pubkey")
    sig = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    verify_document_signature(pub, payload, sig)


def _upsert_signal_version(db: Session, *, ruche_id: bytes, origin_id: str, seq: int) -> None:
    row = db.get(HiveSignalVersions, (ruche_id, origin_id))
    if row is None:
        db.add(HiveSignalVersions(ruche_id=ruche_id, origin_instance_id=origin_id, seq=seq))
        db.flush()
    elif seq > row.seq:
        row.seq = seq


def _latest_local_signal_seq(db: Session, *, ruche_id: bytes, origin_id: str) -> int:
    return int(
        db.scalar(
            select(HiveSignal.seq)
            .where(HiveSignal.ruche_id == ruche_id, HiveSignal.origin_instance_id == origin_id)
            .order_by(HiveSignal.seq.desc())
            .limit(1)
        )
        or 0
    )


def ingest_signed_signals(db: Session, *, ruche_id: bytes, items: list[dict], now: datetime) -> int:
    accepted = 0
    for item in items:
        payload = item.get("payload")
        sig = item.get("signature_b64")
        if not isinstance(payload, dict) or not isinstance(sig, str):
            continue
        try:
            verify_remote_signal(db, ruche_id=ruche_id, payload=payload, signature_b64=sig)
        except (ValueError, Exception):
            continue
        if payload_catalog_epoch(payload) < local_catalog_epoch(db, ruche_id):
            continue
        origin = str(payload["origine"])
        seq = int(payload["seq"])
        latest = _latest_local_signal_seq(db, ruche_id=ruche_id, origin_id=origin)
        if seq <= latest:
            existing = db.scalar(
                select(HiveSignal.id)
                .where(
                    HiveSignal.ruche_id == ruche_id,
                    HiveSignal.origin_instance_id == origin,
                    HiveSignal.seq == seq,
                )
                .limit(1)
            )
            if existing is not None or seq < latest:
                continue
        cle = payload.get("cle")
        if not isinstance(cle, dict):
            continue
        from mediatracker.hive.signal_mini_fiche import sanitize_mini_fiche

        safe_mini = sanitize_mini_fiche(payload.get("mini_fiche"))
        cle_key = cle_hash(cle)
        _compact_signal_rows(db, ruche_id=ruche_id, origin_id=origin, cle_key=cle_key)
        entry = HiveSignal(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=seq,
            created_at=now,
            signal=str(payload["signal"]),
            cle_hash=cle_key,
            cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            signature_b64=sig,
            media_item_id=None,
            mini_fiche_json=(
                json.dumps(safe_mini, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if safe_mini
                else None
            ),
        )
        db.add(entry)
        db.flush()
        _compact_signal_rows(db, ruche_id=ruche_id, origin_id=origin, cle_key=cle_key, keep_id=entry.id)
        _upsert_signal_version(db, ruche_id=ruche_id, origin_id=origin, seq=seq)
        accepted += 1
    return accepted


def signal_version_vector_for_ruche(db: Session, ruche_id: bytes) -> dict[str, int]:
    rows = db.scalars(select(HiveSignalVersions).where(HiveSignalVersions.ruche_id == ruche_id)).all()
    return {str(row.origin_instance_id): int(row.seq) for row in rows}


def purge_signals_for_removed_members(
    db: Session, *, ruche_id: bytes, member_ids: set[str]
) -> int:
    removed = list(
        db.scalars(
            select(HiveSignal.id).where(
                HiveSignal.ruche_id == ruche_id,
                HiveSignal.origin_instance_id.is_not(None),
                HiveSignal.origin_instance_id.not_in(member_ids),
            )
        ).all()
    )
    if not removed:
        return 0
    db.execute(delete(HiveSignal).where(HiveSignal.id.in_(removed)))
    return len(removed)