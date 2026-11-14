"""Vérification entrante des requêtes pair."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveMember
from mediatracker.hive.access import is_active_member
from mediatracker.hive.crypto_id import public_key_from_pem
from mediatracker.hive.peer_auth import (
    HIVE_DATE_HEADER,
    HIVE_NONCE_HEADER,
    HIVE_SIGNATURE_HEADER,
    PeerAuthDenied,
    PeerAuthMalformed,
    PeerRequestContext,
    parse_required_headers,
    verify_clock,
    verify_request_signature,
)
from mediatracker.hive.peer_nonce import consume_nonce


def _header_map(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        out[k] = v
        out[k.lower()] = v
    return out


def member_public_key_pem(db: Session, *, ruche_id: bytes, instance_id: str) -> str:
    row = db.scalar(
        select(HiveMember.public_key_pem)
        .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == instance_id)
        .limit(1)
    )
    if not row:
        raise PeerAuthDenied("unknown_member")
    return str(row)


def authenticate_member_peer(
    db: Session,
    *,
    headers,
    method: str,
    path: str,
    query: str,
    body: bytes,
    ruche_id: bytes,
    now: datetime,
) -> PeerRequestContext:
    hmap = _header_map(headers)
    try:
        ctx = parse_required_headers(hmap, expected_ruche_id=ruche_id)
    except PeerAuthMalformed as exc:
        raise exc
    verify_clock(ctx.date, now=now)
    if not is_active_member(db, ruche_id=ruche_id, instance_id=ctx.hive_id):
        raise PeerAuthDenied("excluded")
    pem = member_public_key_pem(db, ruche_id=ruche_id, instance_id=ctx.hive_id)
    pub = public_key_from_pem(pem.encode("utf-8"))
    sig_b64 = hmap.get(HIVE_SIGNATURE_HEADER) or hmap.get(HIVE_SIGNATURE_HEADER.lower()) or ""
    date_s = hmap.get(HIVE_DATE_HEADER) or hmap.get(HIVE_DATE_HEADER.lower()) or ""
    nonce_s = hmap.get(HIVE_NONCE_HEADER) or hmap.get(HIVE_NONCE_HEADER.lower()) or ""
    verify_request_signature(
        public_key=pub,
        method=method,
        path=path,
        query=query,
        hive_date=date_s,
        nonce_b64=nonce_s,
        signature_b64=sig_b64,
        body=body,
    )
    consume_nonce(db, hive_id=ctx.hive_id, nonce=ctx.nonce, now=now)
    return ctx


def authenticate_join_peer(
    db: Session,
    *,
    headers,
    method: str,
    path: str,
    query: str,
    body: bytes,
    ruche_id: bytes,
    invitee_public_key_pem: str,
    invitee_instance_id: str,
    now: datetime,
) -> PeerRequestContext:
    hmap = _header_map(headers)
    ctx = parse_required_headers(hmap, expected_ruche_id=ruche_id)
    if ctx.hive_id != invitee_instance_id:
        raise PeerAuthDenied("id_mismatch")
    verify_clock(ctx.date, now=now)
    pub = public_key_from_pem(invitee_public_key_pem.encode("utf-8"))
    sig_b64 = hmap.get(HIVE_SIGNATURE_HEADER) or hmap.get(HIVE_SIGNATURE_HEADER.lower()) or ""
    date_s = hmap.get(HIVE_DATE_HEADER) or hmap.get(HIVE_DATE_HEADER.lower()) or ""
    nonce_s = hmap.get(HIVE_NONCE_HEADER) or hmap.get(HIVE_NONCE_HEADER.lower()) or ""
    verify_request_signature(
        public_key=pub,
        method=method,
        path=path,
        query=query,
        hive_date=date_s,
        nonce_b64=nonce_s,
        signature_b64=sig_b64,
        body=body,
    )
    consume_nonce(db, hive_id=ctx.hive_id, nonce=ctx.nonce, now=now)
    return ctx
