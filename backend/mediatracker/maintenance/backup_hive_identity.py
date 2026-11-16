"""Inclusion de la clé Hive (hors SQLite) dans les fichiers de sauvegarde .db."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting
from mediatracker.hive.crypto_id import hive_public_id_from_public_key, private_key_from_pem
from mediatracker.hive.identity import (
    HIVE_ENABLED_KEY,
    HIVE_INSTANCE_NAME_KEY,
    HIVE_NAME_DOCUMENT_KEY,
    HIVE_NAME_SIGNATURE_KEY,
    HIVE_PUBLIC_ADDRESS_KEY,
    load_private_key,
    write_private_key,
)

HIVE_SETTINGS_PRESERVE_ON_IMPORT = frozenset(
    {
        HIVE_ENABLED_KEY,
        HIVE_PUBLIC_ADDRESS_KEY,
        HIVE_INSTANCE_NAME_KEY,
        HIVE_NAME_DOCUMENT_KEY,
        HIVE_NAME_SIGNATURE_KEY,
    }
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


def snapshot_hive_settings(db: Session) -> dict[str, str]:
    """Lecture de l'identité Hive en base avant restauration d'un import."""
    values: dict[str, str] = {}
    for key in HIVE_SETTINGS_PRESERVE_ON_IMPORT:
        row = db.get(Setting, key)
        values[key] = (row.value or "").strip() if row is not None else ""
    return values


def reapply_hive_settings_after_import(db: Session, snapshot: dict[str, str]) -> None:
    """Réinjecte l'identité Hive de l'instance qui reçoit après restauration d'un import."""
    from datetime import datetime, timezone

    moment = datetime.now(timezone.utc)
    for key in HIVE_SETTINGS_PRESERVE_ON_IMPORT:
        value = snapshot.get(key, "")
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value, is_secret=False, updated_at=moment))
        else:
            row.value = value
            row.updated_at = moment


def reapply_receiver_hive_after_import_on_disk(
    live_path: Path,
    safety_path: Path,
    snapshot: dict[str, str],
) -> None:
    """Réinjecte réglages Hive + tables hive_* du receveur en une seule transaction SQLite."""
    from datetime import datetime, timezone

    if not live_path.is_file():
        raise BackupHiveIdentityError("Base active introuvable.")
    moment = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(live_path)
    conn.execute(f"pragma busy_timeout={_BUSY_MS}")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for key in HIVE_SETTINGS_PRESERVE_ON_IMPORT:
            value = snapshot.get(key, "")
            conn.execute(
                """
                INSERT INTO setting (key, value, is_secret, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, moment),
            )
        copy_hive_tables_from_safety(conn, safety_path)
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise BackupHiveIdentityError("Impossible de conserver l'identité Hive du récepteur.") from exc
    finally:
        conn.close()


def copy_hive_tables_from_safety(live_conn: sqlite3.Connection, safety_path: Path) -> None:
    """Recopie toutes les tables hive_* de la copie before-restore vers la base active."""
    if not safety_path.is_file():
        raise BackupHiveIdentityError("Copie de sécurité introuvable pour les tables Hive.")
    live_conn.execute("PRAGMA foreign_keys=OFF")
    src = sqlite3.connect(f"file:{safety_path.resolve()}?mode=ro", uri=True)
    src.execute(f"pragma busy_timeout={_BUSY_MS}")
    try:
        live_names = {
            row[0]
            for row in live_conn.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name GLOB 'hive_*'",
            ).fetchall()
        }
        for name in live_names:
            quoted = name.replace('"', '""')
            live_conn.execute(f'DELETE FROM "{quoted}"')
        safe_tables = [
            row[0]
            for row in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'hive_*'",
            ).fetchall()
        ]
        for name in safe_tables:
            if name not in live_names:
                continue
            quoted = name.replace('"', '""')
            cols = [row[1] for row in src.execute(f'PRAGMA table_info("{quoted}")').fetchall()]
            if not cols:
                continue
            col_sql = ", ".join(f'"{col}"' for col in cols)
            placeholders = ", ".join("?" for _ in cols)
            rows = src.execute(f'SELECT {col_sql} FROM "{quoted}"').fetchall()
            if not rows:
                continue
            live_conn.executemany(
                f'INSERT INTO "{quoted}" ({col_sql}) VALUES ({placeholders})',
                rows,
            )
    finally:
        src.close()
        live_conn.execute("PRAGMA foreign_keys=ON")


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
