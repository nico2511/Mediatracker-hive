"""Appairage entrant via HTTP pair."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveJoinPending
from mediatracker.hive.control_code import control_code_for_pair
from mediatracker.hive.crypto_id import (
    hive_public_id_from_public_key,
    public_key_from_pem,
    public_key_to_pem,
    verify_name_document,
)
from mediatracker.hive.invitations import invitation_row_for_token, validate_invitation_usable
from mediatracker.hive.member_list import current_members_as_records
from mediatracker.hive.membership import creator_member_public_key_pem, find_open_join_pending


def _parse_join_body(raw: bytes) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("bad_body") from exc
    if not isinstance(doc, dict) or doc.get("v") != 1:
        raise ValueError("bad_body")
    return doc


def process_peer_join_preview(
    db: Session,
    *,
    ruche_id: bytes,
    body: bytes,
    now: datetime,
) -> dict:
    doc = _parse_join_body(body)
    token_b64 = doc.get("token_b64")
    if not isinstance(token_b64, str):
        raise ValueError("bad_body")
    token = base64.b64decode(token_b64.encode("ascii"))
    inv = invitation_row_for_token(db, token)
    if inv is None or inv.ruche_id != ruche_id:
        raise ValueError("unknown_token")
    validate_invitation_usable(inv, now=now)
    creator_pem = creator_member_public_key_pem(db, ruche_id)
    member_count = len(current_members_as_records(db, ruche_id))
    return {
        "ruche_id_hex": ruche_id.hex(),
        "member_count": member_count,
        "expires_at": inv.expires_at.astimezone(timezone.utc).isoformat(),
        "creator_public_key_pem": creator_pem,
    }


def process_peer_join_request(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    body: bytes,
    now: datetime,
) -> dict:
    doc = _parse_join_body(body)
    token_b64 = doc.get("token_b64")
    pem = doc.get("invitee_public_key_pem")
    name_doc = doc.get("invitee_name_document")
    name_sig = doc.get("invitee_name_signature_b64")
    address = doc.get("invitee_public_address", "")
    if not all(isinstance(x, str) for x in (token_b64, pem, name_sig)) or not isinstance(name_doc, dict):
        raise ValueError("bad_body")
    token = base64.b64decode(token_b64.encode("ascii"))
    inv = invitation_row_for_token(db, token)
    if inv is None or inv.ruche_id != ruche_id:
        raise ValueError("unknown_token")
    validate_invitation_usable(inv, now=now)
    invitee_pub = public_key_from_pem(pem.encode("utf-8"))
    invitee_id = hive_public_id_from_public_key(invitee_pub)
    sig = base64.b64decode(name_sig.encode("ascii"))
    verify_name_document(invitee_pub, name_doc, sig)
    creator_pem = creator_member_public_key_pem(db, ruche_id)
    creator_pub = public_key_from_pem(creator_pem.encode("utf-8"))
    code = control_code_for_pair(creator_pub, invitee_pub)
    existing = find_open_join_pending(db, ruche_id=ruche_id, invitee_instance_id=invitee_id)
    if existing is not None:
        return {"control_code": code, "pending_id": existing.id, "invitee_instance_id": invitee_id}
    pending = HiveJoinPending(
        ruche_id=ruche_id,
        invitation_id=inv.id,
        invitee_instance_id=invitee_id,
        invitee_public_key_pem=public_key_to_pem(invitee_pub).decode("utf-8"),
        invitee_name_document_json=json.dumps(name_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        invitee_name_signature_b64=name_sig,
        invitee_public_address=str(address or "").strip(),
        control_code=code,
        created_at=now,
    )
    db.add(pending)
    db.flush()
    return {"control_code": code, "pending_id": pending.id, "invitee_instance_id": invitee_id}
