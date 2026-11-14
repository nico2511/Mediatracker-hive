"""Encrypt provider and setting secrets with a key derived from SECRET_KEY."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, secret_key: str) -> str:
    token = _fernet(secret_key).encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_secret(value: str, secret_key: str) -> str:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    raw = value[len(PREFIX) :]
    try:
        return _fernet(secret_key).decrypt(raw.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Secret chiffré illisible.") from exc


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)
