"""Gossip catalogue par ruche : vecteur, pages d'entrées, validation."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveEntry, HiveMember, HiveOriginReachability, HiveSignal, HiveVersions
from mediatracker.hive.access import is_active_member
from mediatracker.hive.catalog_epoch import local_catalog_epoch, payload_catalog_epoch
from mediatracker.hive.catalog_validity import remote_catalog_offer_plausible
from mediatracker.hive.crypto_id import public_key_from_pem, verify_document_signature
from mediatracker.hive.source_offer_mask import clear_mask_for_offer, maybe_lift_source_offer_mask

GOSSIP_PAGE_DEFAULT = 500
_GOSSIP_PAGE = GOSSIP_PAGE_DEFAULT


@dataclass(frozen=True)
class GossipEntryPage:
    entries: list[dict[str, str | dict]]
    has_more: bool
    cursor: dict[str, int | str] | None


def entries_page_for_peer(
    db: Session,
    *,
    ruche_id: bytes,
    peer_vector: dict[str, int],
    limit: int = _GOSSIP_PAGE,
    cursor: dict[str, int | str] | None = None,
) -> GossipEntryPage:
    out: list[dict[str, str | dict]] = []
    start_origin: str | None = None
    start_after_seq = 0
    if cursor is not None:
        o = cursor.get("origin")
        a = cursor.get("after_seq")
        if isinstance(o, str) and isinstance(a, int):
            start_origin = o
            start_after_seq = a
    origins = sorted(
        {
            str(o)
            for o in db.scalars(
                select(HiveEntry.origin_instance_id).where(HiveEntry.ruche_id == ruche_id).distinct()
            ).all()
            if o
        }
    )
    if start_origin is not None and start_origin in origins:
        idx = origins.index(start_origin)
        origins = origins[idx:]
    elif start_origin is not None:
        origins = [o for o in origins if o > start_origin] if start_origin else origins
    for origin in origins:
        peer_seq = int(peer_vector.get(origin, 0))
        if origin == start_origin and start_after_seq:
            min_seq = start_after_seq
        else:
            min_seq = peer_seq
        rows = list(
            db.scalars(
                select(HiveEntry)
                .where(
                    HiveEntry.ruche_id == ruche_id,
                    HiveEntry.origin_instance_id == origin,
                    HiveEntry.seq > min_seq,
                )
                .order_by(HiveEntry.seq.asc())
            ).all()
        )
        for row in rows:
            if not row.payload_json or not row.signature_b64:
                continue
            try:
                payload = json.loads(row.payload_json)
            except json.JSONDecodeError:
                continue
            out.append({"payload": payload, "signature_b64": row.signature_b64})
            if len(out) >= limit:
                return GossipEntryPage(
                    entries=out,
                    has_more=True,
                    cursor={"origin": origin, "after_seq": int(row.seq)},
                )
        start_origin = None
        start_after_seq = 0
    return GossipEntryPage(entries=out, has_more=False, cursor=None)


def entries_missing_for_peer(
    db: Session, *, ruche_id: bytes, peer_vector: dict[str, int], limit: int = _GOSSIP_PAGE
) -> list[dict[str, str | dict]]:
    return entries_page_for_peer(
        db, ruche_id=ruche_id, peer_vector=peer_vector, limit=limit
    ).entries


def _is_tirage_address(address: str) -> bool:
    text = address.strip()
    return not text or text == "tirage"


def version_vector_for_ruche(db: Session, ruche_id: bytes) -> dict[str, int]:
    rows = db.scalars(select(HiveVersions).where(HiveVersions.ruche_id == ruche_id)).all()
    return {str(row.origin_instance_id): int(row.seq) for row in rows}


def _origin_public_key(db: Session, *, ruche_id: bytes, origin_id: str):
    pem = db.scalar(
        select(HiveMember.public_key_pem)
        .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == origin_id)
        .limit(1)
    )
    if not pem:
        return None
    return public_key_from_pem(str(pem).encode("utf-8"))


def _latest_local_seq(db: Session, *, ruche_id: bytes, origin_id: str) -> int:
    return int(
        db.scalar(
            select(HiveEntry.seq)
            .where(HiveEntry.ruche_id == ruche_id, HiveEntry.origin_instance_id == origin_id)
            .order_by(HiveEntry.seq.desc())
            .limit(1)
        )
        or 0
    )


def verify_remote_entry(db: Session, *, ruche_id: bytes, payload: dict, signature_b64: str) -> None:
    rid = payload.get("ruche_id")
    if not isinstance(rid, str) or bytes.fromhex(rid) != ruche_id:
        raise ValueError("bad_ruche")
    origin = payload.get("origine")
    if not isinstance(origin, str):
        raise ValueError("bad_origin")
    if not is_active_member(db, ruche_id=ruche_id, instance_id=origin):
        raise ValueError("unknown_origin")
    addr_row = db.scalar(
        select(HiveMember.public_address)
        .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == origin)
        .limit(1)
    )
    if addr_row is not None and _is_tirage_address(str(addr_row)) and payload.get("etat") == "disponible":
        raise ValueError("tirage_publish")
    pub = _origin_public_key(db, ruche_id=ruche_id, origin_id=origin)
    if pub is None:
        raise ValueError("no_pubkey")
    sig = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    verify_document_signature(pub, payload, sig)


def _upsert_version(db: Session, *, ruche_id: bytes, origin_id: str, seq: int) -> None:
    row = db.get(HiveVersions, (ruche_id, origin_id))
    if row is None:
        db.add(HiveVersions(ruche_id=ruche_id, origin_instance_id=origin_id, seq=seq))
        db.flush()
    elif seq > row.seq:
        row.seq = seq


def ingest_signed_entries(
    db: Session,
    *,
    ruche_id: bytes,
    items: list[dict[str, Any]],
    now: datetime,
    settings: Settings | None = None,
    collected_disponible_cles: list[dict] | None = None,
) -> int:
    accepted = 0
    ingested_disponible_cles: list[dict] = []
    for item in items:
        payload = item.get("payload")
        sig = item.get("signature_b64")
        if not isinstance(payload, dict) or not isinstance(sig, str):
            continue
        try:
            verify_remote_entry(db, ruche_id=ruche_id, payload=payload, signature_b64=sig)
        except (ValueError, Exception):
            continue
        if payload_catalog_epoch(payload) < local_catalog_epoch(db, ruche_id):
            continue
        cle = payload.get("cle")
        fiche = payload.get("fiche")
        etat = str(payload.get("etat") or "disponible")
        sha = payload.get("sha256") if isinstance(payload.get("sha256"), str) else None
        if not isinstance(cle, dict):
            cle = {}
        if etat == "disponible" and not remote_catalog_offer_plausible(
            cle=cle,
            fiche=fiche if isinstance(fiche, dict) else None,
            payload=payload,
            sha256_hex=sha,
            etat=etat,
        ):
            continue
        origin = str(payload["origine"])
        seq = int(payload["seq"])
        latest = _latest_local_seq(db, ruche_id=ruche_id, origin_id=origin)
        if seq <= latest:
            existing = db.scalar(
                select(HiveEntry.id)
                .where(
                    HiveEntry.ruche_id == ruche_id,
                    HiveEntry.origin_instance_id == origin,
                    HiveEntry.seq == seq,
                )
                .limit(1)
            )
            if existing is not None or seq < latest:
                continue
        fiche = payload.get("fiche")
        file_id_raw = payload.get("file_id")
        file_id = bytes.fromhex(file_id_raw) if isinstance(file_id_raw, str) else None
        entry = HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=seq,
            cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if isinstance(cle, dict)
            else None,
            fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if isinstance(fiche, dict)
            else None,
            sha256_hex=payload.get("sha256") if isinstance(payload.get("sha256"), str) else None,
            file_id=bytes.fromhex(payload["file_id"]) if isinstance(payload.get("file_id"), str) else None,
            etat=str(payload.get("etat") or "disponible"),
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            signature_b64=sig,
            media_item_id=None,
            tombstone_at=now if payload.get("etat") == "retire" else None,
        )
        db.add(entry)
        _upsert_version(db, ruche_id=ruche_id, origin_id=origin, seq=seq)
        if file_id is not None:
            if etat == "retire":
                clear_mask_for_offer(db, ruche_id=ruche_id, origin_instance_id=origin, file_id=file_id)
            elif etat == "disponible":
                maybe_lift_source_offer_mask(
                    db,
                    ruche_id=ruche_id,
                    origin_instance_id=origin,
                    file_id=file_id,
                    sha256_hex=sha,
                    seq=seq,
                )
        if etat == "disponible" and isinstance(cle, dict) and cle:
            ingested_disponible_cles.append(cle)
        accepted += 1
    if ingested_disponible_cles:
        if collected_disponible_cles is not None:
            collected_disponible_cles.extend(ingested_disponible_cles)
        elif settings is not None:
            from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store
            from mediatracker.hive.source_download import try_enqueue_hive_for_ingested_offers

            refresh_hive_catalogue_store(db, now=now, settings=settings)
            try_enqueue_hive_for_ingested_offers(
                db,
                settings,
                ruche_id=ruche_id,
                cles=ingested_disponible_cles,
                now=now,
            )
    return accepted


def record_reachability(db: Session, *, ruche_id: bytes, origin_id: str, now: datetime) -> None:
    key = (ruche_id, origin_id)
    row = db.get(HiveOriginReachability, key)
    if row is None:
        db.add(HiveOriginReachability(ruche_id=ruche_id, origin_instance_id=origin_id, last_ok_at=now))
    else:
        row.last_ok_at = now


REACHABILITY_TTL_SECONDS = 3600.0


def mark_origin_unreachable(db: Session, *, ruche_id: bytes, origin_id: str, now: datetime) -> None:
    """Marque une origine comme non joignable (échec de contact pair récent)."""
    from datetime import timedelta

    stale = now - timedelta(seconds=REACHABILITY_TTL_SECONDS + 1.0)
    row = db.get(HiveOriginReachability, (ruche_id, origin_id))
    if row is None:
        db.add(
            HiveOriginReachability(
                ruche_id=ruche_id,
                origin_instance_id=origin_id,
                last_ok_at=stale,
            )
        )
    else:
        row.last_ok_at = stale


def signals_page_for_peer(
    db: Session,
    *,
    ruche_id: bytes,
    peer_vector: dict[str, int],
    limit: int = _GOSSIP_PAGE,
    cursor: dict[str, int | str] | None = None,
) -> GossipEntryPage:
    out: list[dict[str, str | dict]] = []
    start_origin: str | None = None
    start_after_seq = 0
    if cursor is not None:
        o = cursor.get("origin")
        a = cursor.get("after_seq")
        if isinstance(o, str) and isinstance(a, int):
            start_origin = o
            start_after_seq = a
    origins = sorted(
        {
            str(o)
            for o in db.scalars(
                select(HiveSignal.origin_instance_id).where(HiveSignal.ruche_id == ruche_id).distinct()
            ).all()
            if o
        }
    )
    if start_origin is not None and start_origin in origins:
        idx = origins.index(start_origin)
        origins = origins[idx:]
    elif start_origin is not None:
        origins = [o for o in origins if o > start_origin] if start_origin else origins
    for origin in origins:
        peer_seq = int(peer_vector.get(origin, 0))
        min_seq = start_after_seq if origin == start_origin and start_after_seq else peer_seq
        rows = list(
            db.scalars(
                select(HiveSignal)
                .where(
                    HiveSignal.ruche_id == ruche_id,
                    HiveSignal.origin_instance_id == origin,
                    HiveSignal.seq > min_seq,
                )
                .order_by(HiveSignal.seq.asc())
            ).all()
        )
        for row in rows:
            if not row.payload_json or not row.signature_b64:
                continue
            try:
                payload = json.loads(row.payload_json)
            except json.JSONDecodeError:
                continue
            out.append({"payload": payload, "signature_b64": row.signature_b64})
            if len(out) >= limit:
                return GossipEntryPage(
                    entries=out,
                    has_more=True,
                    cursor={"origin": origin, "after_seq": int(row.seq)},
                )
        start_origin = None
        start_after_seq = 0
    return GossipEntryPage(entries=out, has_more=False, cursor=None)


def origin_is_reachable(db: Session, *, ruche_id: bytes, origin_id: str, now: datetime) -> bool:
    from datetime import timezone

    row = db.get(HiveOriginReachability, (ruche_id, origin_id))
    if row is None:
        return False
    seen = row.last_ok_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    moment = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age = (moment - seen.astimezone(timezone.utc)).total_seconds()
    return age < REACHABILITY_TTL_SECONDS
