"""Liste des membres versionnée, signée par le créateur."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mediatracker.db.models import (
    HiveKnownIdentityName,
    HiveMember,
    HiveMemberList,
    HiveRuche,
)
from mediatracker.hive.crypto_id import (
    hive_public_id_from_public_key,
    public_key_from_pem,
    sign_document,
    verify_document_signature,
    verify_name_document,
)


class MemberListError(ValueError):
    pass


def _member_record(
    *,
    instance_id: str,
    public_key_pem: str,
    name_document: dict,
    name_signature_b64: str,
    public_address: str,
    joined_at: datetime,
) -> dict:
    addr = public_address.strip() if public_address.strip() else "tirage"
    return {
        "id": instance_id,
        "public_key_pem": public_key_pem,
        "nom_doc": name_document,
        "nom_sig_b64": name_signature_b64,
        "address": addr,
        "joined": joined_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def build_member_list_payload(*, ruche_id: bytes, version: int, members: list[dict]) -> dict:
    return {
        "v": 1,
        "ruche_id": ruche_id.hex(),
        "version": version,
        "members": members,
    }


def sign_member_list(private_key: Ed25519PrivateKey, payload: dict) -> str:
    sig = sign_document(private_key, payload)
    return base64.b64encode(sig).decode("ascii")


def verify_member_list_signature(creator_pub: Ed25519PublicKey, payload: dict, signature_b64: str) -> None:
    sig = base64.b64decode(signature_b64.encode("ascii"))
    verify_document_signature(creator_pub, payload, sig)


def register_known_name(db: Session, *, instance_id: str, name: str, now: datetime) -> None:
    row = db.get(HiveKnownIdentityName, instance_id)
    if row is None:
        db.add(HiveKnownIdentityName(instance_id=instance_id, name=name, first_seen_at=now))
        return
    if row.name != name:
        raise MemberListError("identity_name_change_rejected")


def validate_member_documents(db: Session, members: list[dict], *, now: datetime) -> list[Ed25519PublicKey]:
    pubs: list[Ed25519PublicKey] = []
    for m in members:
        instance_id = m.get("id")
        pem = m.get("public_key_pem")
        doc = m.get("nom_doc")
        sig_b64 = m.get("nom_sig_b64")
        if not isinstance(instance_id, str) or not isinstance(pem, str):
            raise MemberListError("invalid_member")
        if not isinstance(doc, dict) or not isinstance(sig_b64, str):
            raise MemberListError("invalid_name_doc")
        pub = public_key_from_pem(pem.encode("utf-8"))
        if hive_public_id_from_public_key(pub) != instance_id:
            raise MemberListError("id_pubkey_mismatch")
        sig = base64.b64decode(sig_b64.encode("ascii"))
        try:
            verify_name_document(pub, doc, sig)
        except (ValueError, InvalidSignature) as exc:
            raise MemberListError("bad_name_signature") from exc
        register_known_name(db, instance_id=instance_id, name=str(doc["nom"]), now=now)
        pubs.append(pub)
    return pubs


def latest_member_list_version(db: Session, ruche_id: bytes) -> int:
    m = db.scalar(select(func.max(HiveMemberList.version)).where(HiveMemberList.ruche_id == ruche_id))
    return 0 if m is None else int(m)


def current_members_as_records(db: Session, ruche_id: bytes) -> list[dict]:
    rows = list(db.scalars(select(HiveMember).where(HiveMember.ruche_id == ruche_id)).all())
    out: list[dict] = []
    for row in rows:
        doc = json.loads(row.name_document_json)
        out.append(
            _member_record(
                instance_id=row.member_instance_id,
                public_key_pem=row.public_key_pem,
                name_document=doc,
                name_signature_b64=row.name_signature_b64,
                public_address=row.public_address,
                joined_at=row.joined_at,
            )
        )
    return sorted(out, key=lambda m: m["id"])


def apply_member_list(
    db: Session,
    *,
    ruche_id: bytes,
    creator_id: str,
    creator_public_key: Ed25519PublicKey,
    payload: dict,
    signature_b64: str,
    now: datetime,
) -> HiveMemberList:
    rid = payload.get("ruche_id")
    if not isinstance(rid, str) or bytes.fromhex(rid) != ruche_id:
        raise MemberListError("ruche_mismatch")
    version = payload.get("version")
    if not isinstance(version, int) or version < 1:
        raise MemberListError("bad_version")
    members = payload.get("members")
    if not isinstance(members, list):
        raise MemberListError("bad_members")
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None or ruche.creator_instance_id != creator_id:
        raise MemberListError("not_creator")
    verify_member_list_signature(creator_public_key, payload, signature_b64)
    prev = latest_member_list_version(db, ruche_id)
    if version <= prev:
        raise MemberListError("stale_version")
    validate_member_documents(db, members, now=now)

    db.execute(delete(HiveMember).where(HiveMember.ruche_id == ruche_id))
    for m in members:
        joined_raw = m.get("joined")
        if not isinstance(joined_raw, str):
            raise MemberListError("bad_joined")
        joined = datetime.fromisoformat(joined_raw.replace("Z", "+00:00"))
        addr = m.get("address")
        public_address = "" if addr == "tirage" else str(addr or "")
        db.add(
            HiveMember(
                ruche_id=ruche_id,
                member_instance_id=str(m["id"]),
                joined_at=joined,
                public_address=public_address,
                public_key_pem=str(m["public_key_pem"]),
                name_document_json=json.dumps(m["nom_doc"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                name_signature_b64=str(m["nom_sig_b64"]),
            )
        )
    row = HiveMemberList(
        ruche_id=ruche_id,
        version=version,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        signature_b64=signature_b64,
        published_at=now,
    )
    db.add(row)
    return row


def latest_signed_member_list(db: Session, ruche_id: bytes) -> tuple[dict, str] | None:
    row = db.scalar(
        select(HiveMemberList)
        .where(HiveMemberList.ruche_id == ruche_id)
        .order_by(HiveMemberList.version.desc())
        .limit(1)
    )
    if row is None:
        return None
    payload = json.loads(row.payload_json)
    return payload, row.signature_b64


def apply_member_list_as_peer(
    db: Session,
    *,
    ruche_id: bytes,
    creator_id: str,
    creator_public_key: Ed25519PublicKey,
    payload: dict,
    signature_b64: str,
    now: datetime,
) -> HiveMemberList | None:
    """Importe une liste signée par le créateur (instance invitée)."""
    rid = payload.get("ruche_id")
    if not isinstance(rid, str) or bytes.fromhex(rid) != ruche_id:
        raise MemberListError("ruche_mismatch")
    version = payload.get("version")
    if not isinstance(version, int) or version < 1:
        raise MemberListError("bad_version")
    members = payload.get("members")
    if not isinstance(members, list):
        raise MemberListError("bad_members")
    verify_member_list_signature(creator_public_key, payload, signature_b64)
    prev = latest_member_list_version(db, ruche_id)
    if version <= prev:
        return None
    validate_member_documents(db, members, now=now)

    db.execute(delete(HiveMember).where(HiveMember.ruche_id == ruche_id))
    for m in members:
        joined_raw = m.get("joined")
        if not isinstance(joined_raw, str):
            raise MemberListError("bad_joined")
        joined = datetime.fromisoformat(joined_raw.replace("Z", "+00:00"))
        addr = m.get("address")
        public_address = "" if addr == "tirage" else str(addr or "")
        db.add(
            HiveMember(
                ruche_id=ruche_id,
                member_instance_id=str(m["id"]),
                joined_at=joined,
                public_address=public_address,
                public_key_pem=str(m["public_key_pem"]),
                name_document_json=json.dumps(m["nom_doc"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                name_signature_b64=str(m["nom_sig_b64"]),
            )
        )
    row = HiveMemberList(
        ruche_id=ruche_id,
        version=version,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        signature_b64=signature_b64,
        published_at=now,
    )
    db.add(row)
    db.flush()
    return row


def member_list_for_ruche_a_only(db: Session, ruche_a: bytes, ruche_b: bytes) -> tuple[list[dict], list[dict]]:
    """Retourne (membres A, membres B) — cloisonnement par ruche (tests T3)."""
    ma = current_members_as_records(db, ruche_a)
    mb = current_members_as_records(db, ruche_b)
    return ma, mb
