"""Inclusion de la clé Hive (hors SQLite) dans les fichiers de sauvegarde .db."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mediatracker.config import Settings
from mediatracker.hive.crypto_id import hive_public_id_from_public_key, private_key_from_pem
from mediatracker.hive.identity import (
    HIVE_NAME_DOCUMENT_KEY,
    HIVE_NAME_SIGNATURE_KEY,
    load_private_key,
    write_private_key,
)

logger = logging.getLogger(__name__)

_TABLE = "_mediatracker_backup_hive_identity"
_BUSY_MS = 30_000


class BackupHiveIdentityError(Exception):
    """Bundle Hive illisible ou incohérent avec la base sauvegardée."""


class BackupHiveIdentityIncompleteError(Exception):
    """Identité Hive signée en base mais clé absente — sauvegarde non restaurable."""


@dataclass(frozen=True)
class _Bundle:
    hive_public_id: str
    key_sha256_hex: str
    pem: bytes


def embed_hive_identity_in_backup(backup_path: Path, settings: Settings) -> bool:
    """Ajoute la clé Hive au fichier de sauvegarde (table interne, pas la base active)."""
    conn_probe = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        signed = _signed_identity_in_connection(conn_probe)
    finally:
        conn_probe.close()
    key = load_private_key(settings)
    if signed and key is None:
        raise BackupHiveIdentityIncompleteError(
            "Identité Hive signée en base mais clé privée absente : sauvegarde refusée.",
        )
    if not signed or key is None:
        return False
    from mediatracker.hive.crypto_id import private_key_to_pem

    pem = private_key_to_pem(key)
    digest = hashlib.sha256(pem).hexdigest()
    hive_id = hive_public_id_from_public_key(key.public_key())
    conn = sqlite3.connect(backup_path)
    conn.execute(f"pragma busy_timeout={_BUSY_MS}")
    try:
        if not _signed_identity_in_connection(conn):
            return False
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                hive_public_id TEXT NOT NULL,
                key_sha256_hex TEXT NOT NULL,
                pem BLOB NOT NULL
            )
            """
        )
        conn.execute(f"DELETE FROM {_TABLE}")
        conn.execute(
            f"INSERT INTO {_TABLE} (id, hive_public_id, key_sha256_hex, pem) VALUES (1, ?, ?, ?)",
            (hive_id, digest, pem),
        )
        conn.commit()
        if not _table_exists(conn, _TABLE):
            raise BackupHiveIdentityIncompleteError("Bundle Hive non écrit dans la sauvegarde.")
        return True
    finally:
        conn.close()


def read_hive_identity_bundle(backup_path: Path) -> bytes | None:
    """Lit la clé embarquée ou None (sauvegarde legacy sans bundle)."""
    conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    conn.execute(f"pragma busy_timeout={_BUSY_MS}")
    try:
        if not _table_exists(conn, _TABLE):
            return None
        row = conn.execute(
            f"SELECT hive_public_id, key_sha256_hex, pem FROM {_TABLE} WHERE id = 1",
        ).fetchone()
        if not row:
            raise BackupHiveIdentityError("Bundle Hive vide.")
        hive_id, digest, pem = str(row[0]), str(row[1]), bytes(row[2])
        bundle = _Bundle(hive_public_id=hive_id, key_sha256_hex=digest, pem=pem)
        _validate_bundle(conn, bundle)
        return bundle.pem
    finally:
        conn.close()


def apply_hive_identity_bundle(pem: bytes, settings: Settings) -> None:
    key = private_key_from_pem(pem)
    write_private_key(settings, key)


def strip_hive_identity_bundle_from_live(live_db: Path) -> None:
    """Retire la table interne de bundle après restauration (ne doit pas vivre en base active)."""
    if not live_db.is_file():
        return
    conn = sqlite3.connect(live_db)
    conn.execute(f"pragma busy_timeout={_BUSY_MS}")
    try:
        if _table_exists(conn, _TABLE):
            conn.execute(f"DROP TABLE {_TABLE}")
            conn.commit()
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _signed_identity_in_connection(conn: sqlite3.Connection) -> bool:
    try:
        doc = conn.execute(
            "SELECT value FROM setting WHERE key = ?",
            (HIVE_NAME_DOCUMENT_KEY,),
        ).fetchone()
        sig = conn.execute(
            "SELECT value FROM setting WHERE key = ?",
            (HIVE_NAME_SIGNATURE_KEY,),
        ).fetchone()
    except sqlite3.Error:
        return False
    return bool(doc and doc[0] and sig and sig[0])


def _validate_bundle(conn: sqlite3.Connection, bundle: _Bundle) -> None:
    if hashlib.sha256(bundle.pem).hexdigest() != bundle.key_sha256_hex:
        raise BackupHiveIdentityError("Empreinte du bundle Hive invalide.")
    try:
        key = private_key_from_pem(bundle.pem)
    except ValueError as exc:
        raise BackupHiveIdentityError("Clé Hive illisible dans la sauvegarde.") from exc
    derived = hive_public_id_from_public_key(key.public_key())
    if derived != bundle.hive_public_id:
        raise BackupHiveIdentityError("Identifiant Hive incohérent dans le bundle.")
    if not _signed_identity_in_connection(conn):
        raise BackupHiveIdentityError("Sauvegarde sans identité Hive signée.")
    doc_row = conn.execute(
        "SELECT value FROM setting WHERE key = ?",
        (HIVE_NAME_DOCUMENT_KEY,),
    ).fetchone()
    if not doc_row or not doc_row[0]:
        raise BackupHiveIdentityError("Document d'identité absent dans la sauvegarde.")
    sig_row = conn.execute(
        "SELECT value FROM setting WHERE key = ?",
        (HIVE_NAME_SIGNATURE_KEY,),
    ).fetchone()
    if not sig_row or not sig_row[0]:
        raise BackupHiveIdentityError("Signature d'identité absente dans la sauvegarde.")
    import base64
    import json

    from mediatracker.hive.crypto_id import verify_name_document

    try:
        document = json.loads(str(doc_row[0]))
        signature = base64.b64decode(str(sig_row[0]))
        verify_name_document(key.public_key(), document, signature)
    except (ValueError, json.JSONDecodeError) as exc:
        raise BackupHiveIdentityError("Document d'identité Hive non vérifiable avec la clé embarquée.") from exc
