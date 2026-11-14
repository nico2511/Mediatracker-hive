"""Invitations mtrhive:1: (usage unique, 48 h, empreinte de jeton)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveInvitation, HiveRuche
from mediatracker.hive.crypto_id import (
    canonical_json_bytes,
    hive_public_id_from_public_key,
    public_key_from_pem,
    sign_document,
    verify_document_signature,
)

INVITATION_TTL = timedelta(hours=48)
LINK_PREFIX = "mtrhive:1:"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def token_hash(raw_token: bytes) -> bytes:
    return hashlib.sha256(raw_token).digest()


def build_invitation_document(
    *,
    ruche_id: bytes,
    creator_id: str,
    creator_address: str,
    token: bytes,
    expires_at: datetime,
) -> dict:
    return {
        "v": 1,
        "ruche_id": ruche_id.hex(),
        "creator_id": creator_id,
        "creator_address": creator_address,
        "token_b64": base64.b64encode(token).decode("ascii"),
        "expires": expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def sign_invitation_payload(private_key: Ed25519PrivateKey, payload: dict) -> str:
    sig = sign_document(private_key, payload)
    return base64.b64encode(sig).decode("ascii")


def encode_invitation_link(payload: dict, signature_b64: str) -> str:
    outer = dict(payload)
    outer["signature_b64"] = signature_b64
    blob = _b64url(canonical_json_bytes(outer))
    return f"{LINK_PREFIX}{blob}"


def decode_invitation_link(link: str) -> tuple[dict, str]:
    text = link.strip()
    if not text.startswith(LINK_PREFIX):
        raise ValueError("bad_prefix")
    blob = text[len(LINK_PREFIX) :]
    raw = _b64url_decode(blob)
    outer = json.loads(raw.decode("utf-8"))
    if not isinstance(outer, dict) or outer.get("v") != 1:
        raise ValueError("bad_format")
    sig = outer.pop("signature_b64", None)
    if not isinstance(sig, str):
        raise ValueError("missing_signature")
    return outer, sig


def verify_invitation_signature(creator_public_key_pem: str, payload: dict, signature_b64: str) -> None:
    pub = public_key_from_pem(creator_public_key_pem.encode("utf-8"))
    try:
        sig = base64.b64decode(signature_b64.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_signature") from exc
    try:
        verify_document_signature(pub, payload, sig)
    except Exception as exc:
        raise ValueError("invalid_signature") from exc


def finalize_remote_invitation_preview(*, payload: dict, signature_b64: str, remote: dict) -> dict:
    """Lie la réponse créateur au document d’invitation signé (identité + ruche)."""
    creator_pem = remote.get("creator_public_key_pem")
    if not isinstance(creator_pem, str) or not creator_pem.strip():
        raise ValueError("bad_preview_response")
    creator_id = str(payload.get("creator_id") or "")
    if not creator_id:
        raise ValueError("bad_invitation")
    derived_id = hive_public_id_from_public_key(public_key_from_pem(creator_pem.encode("utf-8")))
    if derived_id != creator_id:
        raise ValueError("creator_identity_mismatch")
    payload_ruche = str(payload.get("ruche_id") or "")
    remote_ruche = str(remote.get("ruche_id_hex") or "")
    if not payload_ruche or remote_ruche != payload_ruche:
        raise ValueError("ruche_mismatch")
    verify_invitation_signature(creator_pem, payload, signature_b64)
    return {
        "ruche_id_hex": remote_ruche,
        "member_count": int(remote.get("member_count") or 0),
        "expires_at": str(remote.get("expires_at") or payload.get("expires") or ""),
    }


def create_invitation(
    db: Session,
    *,
    ruche_id: bytes,
    creator_id: str,
    creator_address: str,
    creator_private_key: Ed25519PrivateKey,
    creator_public_key_pem: str,
    now: datetime,
) -> tuple[HiveInvitation, str, bytes]:
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        raise ValueError("ruche_not_found")
    if ruche.creator_instance_id != creator_id:
        raise ValueError("not_creator")
    if not creator_address.strip():
        raise ValueError("creator_needs_public_address")
    token = secrets.token_bytes(16)
    expires = now + INVITATION_TTL
    payload = build_invitation_document(
        ruche_id=ruche_id,
        creator_id=creator_id,
        creator_address=creator_address.strip(),
        token=token,
        expires_at=expires,
    )
    sig = sign_invitation_payload(creator_private_key, payload)
    verify_invitation_signature(creator_public_key_pem, payload, sig)
    row = HiveInvitation(
        ruche_id=ruche_id,
        token_hash=token_hash(token),
        expires_at=expires,
        created_at=now,
    )
    db.add(row)
    db.flush()
    link = encode_invitation_link(payload, sig)
    return row, link, token


def invitation_row_for_token(db: Session, token: bytes) -> HiveInvitation | None:
    th = token_hash(token)
    return db.scalar(select(HiveInvitation).where(HiveInvitation.token_hash == th).limit(1))


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validate_invitation_usable(inv: HiveInvitation, *, now: datetime) -> None:
    if inv.revoked_at is not None:
        raise ValueError("invitation_revoked")
    if inv.consumed_at is not None:
        raise ValueError("invitation_consumed")
    if _utc(now) >= _utc(inv.expires_at):
        raise ValueError("invitation_expired")


def revoke_invitation(db: Session, *, invitation_id: int, creator_id: str, now: datetime) -> None:
    inv = db.get(HiveInvitation, invitation_id)
    if inv is None:
        raise ValueError("not_found")
    ruche = db.get(HiveRuche, inv.ruche_id)
    if ruche is None or ruche.creator_instance_id != creator_id:
        raise ValueError("not_creator")
    if inv.consumed_at is not None:
        raise ValueError("already_consumed")
    inv.revoked_at = now


def consume_invitation(inv: HiveInvitation, *, now: datetime) -> None:
    validate_invitation_usable(inv, now=now)
    inv.consumed_at = now
