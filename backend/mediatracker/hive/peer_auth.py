"""Signatures ed25519 des requêtes pair Hive (/hive/)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mediatracker.hive.crypto_id import hive_public_id_from_public_key

HIVE_ID_HEADER = "Hive-Id"
HIVE_RUCHE_HEADER = "Hive-Ruche"
HIVE_DATE_HEADER = "Hive-Date"
HIVE_NONCE_HEADER = "Hive-Nonce"
HIVE_SIGNATURE_HEADER = "Hive-Signature"

_CLOCK_SKEW = timedelta(minutes=5)
_NONCE_BYTES = 16

_HV_ID_RE = re.compile(r"^hv1[a-z2-9-]{34,}$")


class PeerAuthMalformed(Exception):
    """En-têtes absents ou mal formés → 404."""


class PeerAuthDenied(Exception):
    """Signature, membre ou nonce invalide → 401."""


@dataclass(frozen=True)
class PeerRequestContext:
    hive_id: str
    ruche_id: bytes
    nonce: bytes
    date: datetime


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def new_nonce_bytes() -> bytes:
    return secrets.token_bytes(_NONCE_BYTES)


def nonce_header_value(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_nonce_header(value: str) -> bytes:
    text = value.strip()
    if not text:
        raise PeerAuthMalformed("nonce")
    pad = "=" * ((4 - len(text) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode((text + pad).encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise PeerAuthMalformed("nonce") from exc
    if len(raw) != _NONCE_BYTES:
        raise PeerAuthMalformed("nonce")
    return raw


def _parse_hive_date(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise PeerAuthMalformed("date")
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise PeerAuthMalformed("date") from exc


def signing_payload(
    *,
    method: str,
    path: str,
    query: str,
    hive_date: str,
    nonce_b64: str,
    body: bytes,
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    lines = [method.upper(), path, query, hive_date, nonce_b64, body_hash]
    return "\n".join(lines).encode("utf-8")


def sign_request_headers(
    *,
    private_key: Ed25519PrivateKey,
    method: str,
    path: str,
    query: str,
    ruche_id: bytes,
    body: bytes,
    when: datetime | None = None,
) -> dict[str, str]:
    moment = _utc(when or datetime.now(timezone.utc))
    hive_date = moment.isoformat().replace("+00:00", "Z")
    nonce_raw = new_nonce_bytes()
    nonce_b64 = nonce_header_value(nonce_raw)
    payload = signing_payload(
        method=method, path=path, query=query, hive_date=hive_date, nonce_b64=nonce_b64, body=body
    )
    sig = private_key.sign(payload)
    hive_id = hive_public_id_from_public_key(private_key.public_key())
    return {
        HIVE_ID_HEADER: hive_id,
        HIVE_RUCHE_HEADER: ruche_id.hex(),
        HIVE_DATE_HEADER: hive_date,
        HIVE_NONCE_HEADER: nonce_b64,
        HIVE_SIGNATURE_HEADER: base64.b64encode(sig).decode("ascii"),
    }


def parse_required_headers(headers: dict[str, str], *, expected_ruche_id: bytes) -> PeerRequestContext:
    hive_id = headers.get(HIVE_ID_HEADER) or headers.get(HIVE_ID_HEADER.lower())
    ruche_hex = headers.get(HIVE_RUCHE_HEADER) or headers.get(HIVE_RUCHE_HEADER.lower())
    hive_date = headers.get(HIVE_DATE_HEADER) or headers.get(HIVE_DATE_HEADER.lower())
    nonce_raw = headers.get(HIVE_NONCE_HEADER) or headers.get(HIVE_NONCE_HEADER.lower())
    signature = headers.get(HIVE_SIGNATURE_HEADER) or headers.get(HIVE_SIGNATURE_HEADER.lower())
    if not all(isinstance(x, str) and x.strip() for x in (hive_id, ruche_hex, hive_date, nonce_raw, signature)):
        raise PeerAuthMalformed("headers")
    if not _HV_ID_RE.match(hive_id.strip()):
        raise PeerAuthMalformed("id")
    try:
        ruche_id = bytes.fromhex(ruche_hex.strip())
    except ValueError as exc:
        raise PeerAuthMalformed("ruche") from exc
    if ruche_id != expected_ruche_id:
        raise PeerAuthMalformed("ruche")
    if len(ruche_id) != 16:
        raise PeerAuthMalformed("ruche")
    when = _parse_hive_date(hive_date)
    nonce = parse_nonce_header(nonce_raw)
    return PeerRequestContext(hive_id=hive_id.strip(), ruche_id=ruche_id, nonce=nonce, date=when)


def verify_clock(date: datetime, *, now: datetime) -> None:
    a = _utc(now)
    b = _utc(date)
    if abs(a - b) > _CLOCK_SKEW:
        raise PeerAuthDenied("clock")


def verify_request_signature(
    *,
    public_key: Ed25519PublicKey,
    method: str,
    path: str,
    query: str,
    hive_date: str,
    nonce_b64: str,
    signature_b64: str,
    body: bytes,
) -> None:
    payload = signing_payload(
        method=method, path=path, query=query, hive_date=hive_date, nonce_b64=nonce_b64, body=body
    )
    try:
        sig = base64.b64decode(signature_b64.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise PeerAuthDenied("sig") from exc
    try:
        public_key.verify(sig, payload)
    except Exception as exc:
        raise PeerAuthDenied("sig") from exc
