"""Identifiant Hive `hv1…` et document de nom signé (ed25519)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

# Base32 RFC 4648 sans remplissage ; minuscules pour un affichage lisible.
_BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

_HV_NAME_RE = re.compile(r"^.{2,32}$")


def hive_public_id_from_public_key(pub: Ed25519PublicKey) -> str:
    raw = pub.public_bytes_raw()
    digest = hashlib.sha256(raw).digest()[:20]
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=").lower()
    # 32 caractères base32 sur 20 octets d'entrée
    body = "".join(encoded[i : i + 4] + ("-" if i + 4 < len(encoded) else "") for i in range(0, len(encoded), 4))
    return f"hv1{body}"


def parse_hive_instance_id(value: str) -> str | None:
    if not value.startswith("hv1"):
        return None
    rest = value[3:]
    compact = rest.replace("-", "")
    if len(compact) != 32 or any(c not in _BASE32_ALPHABET for c in compact):
        return None
    return value


def validate_instance_name(name: str) -> str:
    text = name.strip()
    if not _HV_NAME_RE.match(text):
        raise ValueError("Le nom doit comporter entre 2 et 32 caractères.")
    return text


def canonical_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_name_document(*, instance_id: str, name: str, signed_at: datetime) -> dict:
    return {
        "v": 1,
        "id": instance_id,
        "nom": name,
        "date": signed_at.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def sign_document(private_key: Ed25519PrivateKey, document: dict) -> bytes:
    payload = canonical_json_bytes(document)
    return private_key.sign(payload)


def verify_document_signature(pub: Ed25519PublicKey, document: dict, signature: bytes) -> None:
    """Vérifie une signature ed25519 sur le JSON canonique du document."""
    payload = canonical_json_bytes(document)
    pub.verify(signature, payload)


def verify_name_document(pub: Ed25519PublicKey, document: dict, signature: bytes) -> None:
    instance_id = document.get("id")
    if not isinstance(instance_id, str) or parse_hive_instance_id(instance_id) is None:
        raise ValueError("Identifiant invalide dans le document.")
    if hive_public_id_from_public_key(pub) != instance_id:
        raise ValueError("Identifiant incohérent avec la clé publique.")
    name = document.get("nom")
    if not isinstance(name, str):
        raise ValueError("Nom manquant dans le document.")
    validate_instance_name(name)
    verify_document_signature(pub, document, signature)


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def private_key_to_pem(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def private_key_from_pem(data: bytes) -> Ed25519PrivateKey:
    loaded = serialization.load_pem_private_key(data, password=None)
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ValueError("Clé Hive invalide.")
    return loaded


def public_key_from_pem(data: bytes) -> Ed25519PublicKey:
    loaded = serialization.load_pem_public_key(data)
    if not isinstance(loaded, Ed25519PublicKey):
        raise ValueError("Clé publique Hive invalide.")
    return loaded


def public_key_to_pem(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
