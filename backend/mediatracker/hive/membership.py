"""Création de ruches, appairage, exclusion et départ (T3)."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import (
    HiveEntry,
    HiveInvitation,
    HiveJoinPending,
    HiveMember,
    HiveRuche,
    HiveRucheExcluded,
)
from mediatracker.hive.catalog_epoch import member_list_epoch_fields, ruche_confirm_name
from mediatracker.hive.control_code import control_code_for_pair
from mediatracker.hive.crypto_id import (
    build_name_document,
    hive_public_id_from_public_key,
    public_key_from_pem,
    public_key_to_pem,
    sign_document,
    verify_name_document,
)
from mediatracker.hive.identity import (
    HIVE_NAME_DOCUMENT_KEY,
    HIVE_NAME_SIGNATURE_KEY,
    HIVE_PUBLIC_ADDRESS_KEY,
    _read_setting_text,  # noqa: SLF001
    load_private_key,
)
from mediatracker.hive.invitations import (
    consume_invitation,
    decode_invitation_link,
    invitation_row_for_token,
    validate_invitation_usable,
    verify_invitation_signature,
)
from mediatracker.hive.member_list import (
    apply_member_list,
    build_member_list_payload,
    current_members_as_records,
    latest_member_list_version,
    sign_member_list,
)


def new_ruche_id() -> bytes:
    return secrets.token_bytes(16)


def local_instance_identity(
    db: Session, settings: Settings
) -> tuple[Ed25519PrivateKey, str, dict, str, str, str]:
    pk = load_private_key(settings)
    if pk is None:
        raise ValueError("no_identity")
    instance_id = hive_public_id_from_public_key(pk.public_key())
    doc_raw = _read_setting_text(db, HIVE_NAME_DOCUMENT_KEY)
    sig_raw = _read_setting_text(db, HIVE_NAME_SIGNATURE_KEY)
    if not doc_raw or not sig_raw:
        raise ValueError("no_name_document")
    doc = json.loads(doc_raw)
    pem = public_key_to_pem(pk.public_key()).decode("utf-8")
    address = _read_setting_text(db, HIVE_PUBLIC_ADDRESS_KEY)
    return pk, instance_id, doc, sig_raw, pem, address


def create_ruche(db: Session, settings: Settings, *, now: datetime) -> HiveRuche:
    pk, instance_id, doc, sig_b64, pem, address = local_instance_identity(db, settings)
    if not address.strip():
        raise ValueError("public_address_required")
    ruche_id = new_ruche_id()
    ruche = HiveRuche(ruche_id=ruche_id, creator_instance_id=instance_id, created_at=now)
    db.add(ruche)
    db.flush()
    members = [
        {
            "id": instance_id,
            "public_key_pem": pem,
            "nom_doc": doc,
            "nom_sig_b64": sig_b64,
            "address": address.strip(),
            "joined": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    ]
    version = 1
    catalog_epoch, catalog_reset_at = member_list_epoch_fields(db, ruche_id)
    payload = build_member_list_payload(
        ruche_id=ruche_id,
        version=version,
        members=members,
        catalog_epoch=catalog_epoch,
        catalog_reset_at=catalog_reset_at,
    )
    sig = sign_member_list(pk, payload)
    apply_member_list(
        db,
        ruche_id=ruche_id,
        creator_id=instance_id,
        creator_public_key=pk.public_key(),
        payload=payload,
        signature_b64=sig,
        now=now,
        settings=settings,
    )
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)
    return ruche


def creator_member_public_key_pem(db: Session, ruche_id: bytes) -> str:
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None or not ruche.creator_instance_id:
        raise ValueError("ruche_not_found")
    row = db.scalar(
        select(HiveMember)
        .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == ruche.creator_instance_id)
        .limit(1)
    )
    if row is None or not row.public_key_pem:
        raise ValueError("creator_not_in_members")
    return row.public_key_pem


def preview_invitation_link(
    db: Session, settings: Settings, *, link: str, now: datetime
) -> dict:
    payload, sig = decode_invitation_link(link)
    token = base64.b64decode(payload["token_b64"].encode("ascii"))
    inv = invitation_row_for_token(db, token)
    if inv is None:
        from mediatracker.hive.peer_membership import preview_invitation_over_http

        return preview_invitation_over_http(db, settings, link=link, now=now)
    creator_pem = creator_member_public_key_pem(db, inv.ruche_id)
    verify_invitation_signature(creator_pem, payload, sig)
    validate_invitation_usable(inv, now=now)
    expires = datetime.fromisoformat(payload["expires"].replace("Z", "+00:00"))
    member_count = len(current_members_as_records(db, inv.ruche_id))
    return {
        "ruche_id_hex": inv.ruche_id.hex(),
        "member_count": member_count,
        "expires_at": expires.isoformat(),
    }


def begin_join(
    db: Session,
    settings: Settings,
    *,
    link: str,
    invitee_private_key: Ed25519PrivateKey,
    invitee_name: str,
    invitee_public_address: str,
    now: datetime,
) -> tuple[str, str, int]:
    """Simule l'étape invité : retourne (control_code, invitee_instance_id, pending_id)."""
    payload, sig = decode_invitation_link(link)
    token = base64.b64decode(payload["token_b64"].encode("ascii"))
    inv = invitation_row_for_token(db, token)
    if inv is None:
        raise ValueError("unknown_token")
    creator_pem = creator_member_public_key_pem(db, inv.ruche_id)
    verify_invitation_signature(creator_pem, payload, sig)
    validate_invitation_usable(inv, now=now)
    ruche_id = bytes.fromhex(payload["ruche_id"])
    creator_pub = public_key_from_pem(creator_pem.encode("utf-8"))
    invitee_pub = invitee_private_key.public_key()
    invitee_id = hive_public_id_from_public_key(invitee_pub)
    name_doc = build_name_document(instance_id=invitee_id, name=invitee_name, signed_at=now)
    name_sig = sign_document(invitee_private_key, name_doc)
    verify_name_document(invitee_pub, name_doc, name_sig)
    code = control_code_for_pair(creator_pub, invitee_pub)
    existing = find_open_join_pending(db, ruche_id=ruche_id, invitee_instance_id=invitee_id)
    if existing is not None:
        return code, invitee_id, existing.id
    pending = HiveJoinPending(
        ruche_id=ruche_id,
        invitation_id=inv.id,
        invitee_instance_id=invitee_id,
        invitee_public_key_pem=public_key_to_pem(invitee_pub).decode("utf-8"),
        invitee_name_document_json=json.dumps(name_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        invitee_name_signature_b64=base64.b64encode(name_sig).decode("ascii"),
        invitee_public_address=invitee_public_address.strip(),
        control_code=code,
        created_at=now,
    )
    db.add(pending)
    db.flush()
    return code, invitee_id, pending.id


def creator_control_code_for_pending(
    db: Session,
    settings: Settings,
    *,
    pending_id: int,
) -> str:
    pk, _, _, _, pem, _ = local_instance_identity(db, settings)
    pending = db.get(HiveJoinPending, pending_id)
    if pending is None or pending.consumed_at is not None or pending.rejected_at is not None:
        raise ValueError("pending_not_found")
    creator_pub = pk.public_key()
    invitee_pub = public_key_from_pem(pending.invitee_public_key_pem.encode("utf-8"))
    return control_code_for_pair(creator_pub, invitee_pub)


def accept_join_pending(
    db: Session,
    settings: Settings,
    *,
    pending_id: int,
    now: datetime,
) -> None:
    pk, creator_id, _, _, _, _ = local_instance_identity(db, settings)
    pending = db.get(HiveJoinPending, pending_id)
    if pending is None or pending.consumed_at is not None or pending.rejected_at is not None:
        raise ValueError("pending_not_found")
    ruche = db.get(HiveRuche, pending.ruche_id)
    if ruche is None or ruche.creator_instance_id != creator_id:
        raise ValueError("not_creator")
    inv = db.get(HiveInvitation, pending.invitation_id)
    if inv is None:
        raise ValueError("invitation_missing")
    consume_invitation(inv, now=now)
    members = current_members_as_records(db, pending.ruche_id)
    invitee_doc = json.loads(pending.invitee_name_document_json)
    members.append(
        {
            "id": pending.invitee_instance_id,
            "public_key_pem": pending.invitee_public_key_pem,
            "nom_doc": invitee_doc,
            "nom_sig_b64": pending.invitee_name_signature_b64,
            "address": pending.invitee_public_address.strip() or "tirage",
            "joined": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    )
    version = latest_member_list_version(db, pending.ruche_id) + 1
    catalog_epoch, catalog_reset_at = member_list_epoch_fields(db, pending.ruche_id)
    payload = build_member_list_payload(
        ruche_id=pending.ruche_id,
        version=version,
        members=members,
        catalog_epoch=catalog_epoch,
        catalog_reset_at=catalog_reset_at,
    )
    sig = sign_member_list(pk, payload)
    apply_member_list(
        db,
        ruche_id=pending.ruche_id,
        creator_id=creator_id,
        creator_public_key=pk.public_key(),
        payload=payload,
        signature_b64=sig,
        now=now,
        settings=settings,
    )
    duplicate_ids = [
        row.id
        for row in open_pending_rows_for_invitee(
            db, ruche_id=pending.ruche_id, invitee_instance_id=pending.invitee_instance_id
        )
        if row.id != pending.id
    ]
    pending.consumed_at = now
    for dup_id in duplicate_ids:
        dup = db.get(HiveJoinPending, dup_id)
        if dup is not None and dup.consumed_at is None and dup.rejected_at is None:
            dup.rejected_at = now
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)


def reject_join_pending(
    db: Session,
    settings: Settings,
    *,
    pending_id: int,
    now: datetime,
) -> None:
    _, creator_id, *_ = local_instance_identity(db, settings)
    pending = db.get(HiveJoinPending, pending_id)
    if pending is None or pending.consumed_at is not None or pending.rejected_at is not None:
        raise ValueError("pending_not_found")
    ruche = db.get(HiveRuche, pending.ruche_id)
    if ruche is None or ruche.creator_instance_id != creator_id:
        raise ValueError("not_creator")
    for row in open_pending_rows_for_invitee(
        db, ruche_id=pending.ruche_id, invitee_instance_id=pending.invitee_instance_id
    ):
        row.rejected_at = now


def open_pending_rows_for_invitee(
    db: Session, *, ruche_id: bytes, invitee_instance_id: str
) -> list[HiveJoinPending]:
    return list(
        db.scalars(
            select(HiveJoinPending)
            .where(
                HiveJoinPending.ruche_id == ruche_id,
                HiveJoinPending.invitee_instance_id == invitee_instance_id,
                HiveJoinPending.consumed_at.is_(None),
                HiveJoinPending.rejected_at.is_(None),
            )
            .order_by(HiveJoinPending.id)
        ).all()
    )


def find_open_join_pending(
    db: Session, *, ruche_id: bytes, invitee_instance_id: str
) -> HiveJoinPending | None:
    rows = open_pending_rows_for_invitee(db, ruche_id=ruche_id, invitee_instance_id=invitee_instance_id)
    return rows[-1] if rows else None


def purge_entries_for_removed_members(
    db: Session, *, ruche_id: bytes, member_ids: set[str], now: datetime
) -> int:
    """Retire du cache les origines absentes d'une liste de membres en vigueur."""
    origins = list(
        db.scalars(
            select(HiveEntry.origin_instance_id)
            .where(HiveEntry.ruche_id == ruche_id)
            .distinct()
        ).all()
    )
    purged = 0
    for origin in origins:
        if not origin or str(origin) in member_ids:
            continue
        purged += purge_origin_entries_in_ruche(db, ruche_id=ruche_id, origin_id=str(origin), now=now)
    return purged


def purge_origin_entries_in_ruche(db: Session, *, ruche_id: bytes, origin_id: str, now: datetime) -> int:
    """Retire du cache catalogue local les entrées d'un membre (exclusion)."""
    rows = list(
        db.scalars(
            select(HiveEntry).where(
                HiveEntry.ruche_id == ruche_id,
                HiveEntry.origin_instance_id == origin_id,
                HiveEntry.etat == "disponible",
            )
        ).all()
    )
    for row in rows:
        row.etat = "retire"
        row.tombstone_at = now
    return len(rows)


def exclude_member(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    target_instance_id: str,
    now: datetime,
) -> None:
    pk, creator_id, _, _, _, _ = local_instance_identity(db, settings)
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None or ruche.creator_instance_id != creator_id:
        raise ValueError("not_creator")
    if target_instance_id == creator_id:
        raise ValueError("cannot_exclude_self")
    members = [m for m in current_members_as_records(db, ruche_id) if m["id"] != target_instance_id]
    if len(members) == len(current_members_as_records(db, ruche_id)):
        raise ValueError("member_not_found")
    version = latest_member_list_version(db, ruche_id) + 1
    catalog_epoch, catalog_reset_at = member_list_epoch_fields(db, ruche_id)
    payload = build_member_list_payload(
        ruche_id=ruche_id,
        version=version,
        members=members,
        catalog_epoch=catalog_epoch,
        catalog_reset_at=catalog_reset_at,
    )
    sig = sign_member_list(pk, payload)
    apply_member_list(
        db,
        ruche_id=ruche_id,
        creator_id=creator_id,
        creator_public_key=pk.public_key(),
        payload=payload,
        signature_b64=sig,
        now=now,
        settings=settings,
    )
    existing = db.scalar(
        select(HiveRucheExcluded.id)
        .where(HiveRucheExcluded.ruche_id == ruche_id, HiveRucheExcluded.instance_id == target_instance_id)
        .limit(1)
    )
    if existing is None:
        db.add(HiveRucheExcluded(ruche_id=ruche_id, instance_id=target_instance_id, excluded_at=now))
    purge_origin_entries_in_ruche(db, ruche_id=ruche_id, origin_id=target_instance_id, now=now)
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)


def leave_ruche(db: Session, settings: Settings, *, ruche_id: bytes, now: datetime) -> None:
    pk, instance_id, _, _, _, _ = local_instance_identity(db, settings)
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        raise ValueError("ruche_not_found")
    if ruche.creator_instance_id == instance_id:
        raise ValueError("creator_cannot_leave")
    if (
        db.scalar(
            select(HiveMember.id)
            .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == instance_id)
            .limit(1)
        )
        is None
    ):
        raise ValueError("not_member")
    purge_origin_entries_in_ruche(db, ruche_id=ruche_id, origin_id=instance_id, now=now)
    db.execute(
        delete(HiveMember).where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == instance_id)
    )
    db.add(HiveRucheExcluded(ruche_id=ruche_id, instance_id=instance_id, excluded_at=now))
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)


def ruches_for_instance(db: Session, instance_id: str) -> list[HiveRuche]:
    ruche_ids = list(
        db.scalars(select(HiveMember.ruche_id).where(HiveMember.member_instance_id == instance_id)).all()
    )
    out: list[HiveRuche] = []
    for rid in dict.fromkeys(ruche_ids):
        r = db.get(HiveRuche, rid)
        if r is not None:
            out.append(r)
    return out


def pending_for_ruche(db: Session, ruche_id: bytes) -> list[HiveJoinPending]:
    rows = list(
        db.scalars(
            select(HiveJoinPending)
            .where(
                HiveJoinPending.ruche_id == ruche_id,
                HiveJoinPending.consumed_at.is_(None),
                HiveJoinPending.rejected_at.is_(None),
            )
            .order_by(HiveJoinPending.id)
        ).all()
    )
    seen: set[str] = set()
    out: list[HiveJoinPending] = []
    for row in rows:
        if row.invitee_instance_id in seen:
            continue
        seen.add(row.invitee_instance_id)
        out.append(row)
    return out


def open_pending_count_for_ruche(db: Session, ruche_id: bytes) -> int:
    return len(pending_for_ruche(db, ruche_id))


def reset_ruche_catalog(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    confirm_name: str,
    now: datetime,
) -> HiveRuche:
    pk, creator_id, _, _, _, _ = local_instance_identity(db, settings)
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None or ruche.creator_instance_id != creator_id:
        raise ValueError("not_creator")
    expected = ruche_confirm_name(ruche_id)
    if confirm_name.strip() != expected:
        raise ValueError("confirm_name_mismatch")
    new_epoch = int(ruche.catalog_epoch or 0) + 1
    reset_at_iso = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    members = current_members_as_records(db, ruche_id)
    version = latest_member_list_version(db, ruche_id) + 1
    payload = build_member_list_payload(
        ruche_id=ruche_id,
        version=version,
        members=members,
        catalog_epoch=new_epoch,
        catalog_reset_at=reset_at_iso,
    )
    sig = sign_member_list(pk, payload)
    apply_member_list(
        db,
        ruche_id=ruche_id,
        creator_id=creator_id,
        creator_public_key=pk.public_key(),
        payload=payload,
        signature_b64=sig,
        now=now,
        settings=settings,
    )
    from mediatracker.hive.gossip_burst import mark_local_catalog_change

    mark_local_catalog_change(db, ruche_id=ruche_id, now=now)
    db.flush()
    refreshed = db.get(HiveRuche, ruche_id)
    if refreshed is None:
        raise ValueError("ruche_not_found")
    return refreshed
