"""Code de contrôle d'appairage (8 caractères dérivés des deux clés)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def control_code_for_pair(creator_pub: Ed25519PublicKey, invitee_pub: Ed25519PublicKey) -> str:
    a = creator_pub.public_bytes_raw()
    b = invitee_pub.public_bytes_raw()
    first, second = (a, b) if a <= b else (b, a)
    digest = hashlib.sha256(first + b"\0" + second).digest()
    code = base64.b32encode(digest[:5]).decode("ascii").rstrip("=")
    return code[:8].upper()
