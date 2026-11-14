"""Personal API tokens: mt_<prefix>_<secret>, stored as SHA-256."""

import hashlib
import secrets

TOKEN_SCOPES = frozenset({"read", "write", "admin"})


def generate_token() -> tuple[str, str, str]:
    """Return (full_token, prefix, sha256_hex). The full token is shown once."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f"mt_{prefix}_{secret}"
    digest = hash_token(full)
    return full, prefix, digest


def hash_token(full: str) -> str:
    return hashlib.sha256(full.encode("utf-8")).hexdigest()


def split_token(full: str) -> str | None:
    parts = full.split("_", 2)
    if len(parts) != 3 or parts[0] != "mt" or not parts[1] or not parts[2]:
        return None
    return parts[1]


def expand_scopes(scopes: set[str]) -> set[str]:
    expanded = set(scopes)
    if "admin" in expanded:
        expanded |= {"read", "write"}
    elif "write" in expanded:
        expanded.add("read")
    return expanded
