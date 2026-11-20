"""Adhésion via HTTP pair (soumission et confirmation)."""

from __future__ import annotations

import base64
import json
from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveRuche
from mediatracker.hive.crypto_id import (
    build_name_document,
    public_key_from_pem,
    sign_document,
    verify_name_document,
)
from mediatracker.hive.invitations import decode_invitation_link, finalize_remote_invitation_preview
from mediatracker.hive.member_list import apply_member_list_as_peer
from mediatracker.hive.membership import local_instance_identity
from mediatracker.hive.peer_client import signed_peer_request
from mediatracker.hive.peer_outbound import validate_hive_peer_base_url
from mediatracker.security.ssrf import OutboundUrlRejected


def preview_invitation_over_http(
    db: Session,
    settings: Settings,
    *,
    link: str,
    now: datetime,
) -> dict:
    payload, sig = decode_invitation_link(link)
    creator_address = str(payload.get("creator_address") or "").strip()
    if not creator_address:
        raise ValueError("no_creator_address")
    try:
        validate_hive_peer_base_url(creator_address)
    except OutboundUrlRejected as exc:
        raise ValueError("peer_destination_rejected") from exc
    ruche_id = bytes.fromhex(payload["ruche_id"])
    token_b64 = payload["token_b64"]
    pk, instance_id, _doc, _sig_name, pem, _addr = local_instance_identity(db, settings)
    body_doc = {
        "v": 1,
        "token_b64": token_b64,
        "invitee_public_key_pem": pem,
    }
    body = json.dumps(body_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = f"/hive/ruche/{ruche_id.hex()}/join/preview"
    resp = signed_peer_request(
        base_url=creator_address,
        method="POST",
        path=path,
        ruche_id=ruche_id,
        private_key=pk,
        body=body,
    )
    if resp.status_code != 200:
        detail = ""
        try:
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                detail = data["error"]
        except (json.JSONDecodeError, ValueError):
            pass
        if detail:
            raise ValueError(detail)
        raise ValueError(f"peer_preview_failed:{resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("bad_preview_response")
    return finalize_remote_invitation_preview(payload=payload, signature_b64=sig, remote=data)


def submit_join_over_http(
    db: Session,
    settings: Settings,
    *,
    link: str,
    invitee_name: str,
    invitee_public_address: str,
    now: datetime,
) -> dict:
    payload, _sig = decode_invitation_link(link)
    creator_address = str(payload.get("creator_address") or "").strip()
    if not creator_address:
        raise ValueError("no_creator_address")
    try:
        validate_hive_peer_base_url(creator_address)
    except OutboundUrlRejected as exc:
        raise ValueError("peer_destination_rejected") from exc
    ruche_id = bytes.fromhex(payload["ruche_id"])
    token_b64 = payload["token_b64"]
    pk, instance_id, _doc, _sig_name, pem, _addr = local_instance_identity(db, settings)
    name_doc = build_name_document(instance_id=instance_id, name=invitee_name, signed_at=now)
    name_sig = sign_document(pk, name_doc)
    verify_name_document(pk.public_key(), name_doc, name_sig)
    body_doc = {
        "v": 1,
        "token_b64": token_b64,
        "invitee_public_key_pem": pem,
        "invitee_name_document": name_doc,
        "invitee_name_signature_b64": base64.b64encode(name_sig).decode("ascii"),
        "invitee_public_address": invitee_public_address.strip(),
    }
    body = json.dumps(body_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = f"/hive/ruche/{ruche_id.hex()}/join/request"
    resp = signed_peer_request(
        base_url=creator_address,
        method="POST",
        path=path,
        ruche_id=ruche_id,
        private_key=pk,
        body=body,
    )
    if resp.status_code != 200:
        raise ValueError(f"peer_join_failed:{resp.status_code}")
    data = resp.json()
    if db.get(HiveRuche, ruche_id) is None:
        creator_id = str(payload.get("creator_id") or "")
        db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=creator_id or None, created_at=now))
        db.flush()
    return {
        "control_code": data.get("control_code"),
        "pending_id": data.get("pending_id"),
        "invitee_instance_id": data.get("invitee_instance_id") or instance_id,
        "creator_base_url": creator_address,
        "ruche_id_hex": ruche_id.hex(),
    }


def confirm_membership_from_creator(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    creator_base_url: str,
    creator_instance_id: str,
    now: datetime,
) -> int:
    pk, instance_id, *_ = local_instance_identity(db, settings)
    path = f"/hive/ruche/{ruche_id.hex()}/member-list"
    resp = signed_peer_request(
        base_url=creator_base_url,
        method="GET",
        path=path,
        ruche_id=ruche_id,
        private_key=pk,
        body=b"",
    )
    if resp.status_code == 401:
        raise PermissionError("member_not_allowed")
    if resp.status_code != 200:
        raise ValueError(f"peer_list_failed:{resp.status_code}")
    data = resp.json()
    list_payload = data.get("payload")
    list_sig = data.get("signature_b64")
    if not isinstance(list_payload, dict) or not isinstance(list_sig, str):
        raise ValueError("bad_member_list")
    members = list_payload.get("members") or []
    creator_rec = next((m for m in members if m.get("id") == creator_instance_id), None)
    if creator_rec is None:
        raise ValueError("creator_not_in_list")
    creator_pub = public_key_from_pem(str(creator_rec["public_key_pem"]).encode("utf-8"))
    row = apply_member_list_as_peer(
        db,
        ruche_id=ruche_id,
        creator_id=creator_instance_id,
        creator_public_key=creator_pub,
        payload=list_payload,
        signature_b64=list_sig,
        now=now,
        settings=settings,
    )
    db.flush()
    member_ids = {str(m.get("id")) for m in list_payload.get("members", []) if isinstance(m, dict)}
    if instance_id not in member_ids:
        raise ValueError("not_in_imported_list")
    if not is_local_member(db, ruche_id=ruche_id, instance_id=instance_id):
        raise ValueError("not_in_imported_list")
    if row is None:
        from mediatracker.hive.member_list import latest_member_list_version

        return latest_member_list_version(db, ruche_id)
    return int(row.version)


def is_local_member(db: Session, *, ruche_id: bytes, instance_id: str) -> bool:
    from mediatracker.hive.access import is_active_member

    return is_active_member(db, ruche_id=ruche_id, instance_id=instance_id)
