"""Stockage disque et activation de l'identité Hive (ed25519)."""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import (
    HiveInvitation,
    HiveJoinPending,
    HiveKnownIdentityName,
    HiveMember,
    HiveMemberList,
    HivePeerNonce,
    HiveRucheExcluded,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.hive.crypto_id import (
    build_name_document,
    generate_keypair,
    hive_public_id_from_public_key,
    private_key_from_pem,
    private_key_to_pem,
    public_key_to_pem,
    sign_document,
    validate_instance_name,
    verify_name_document,
)

logger = logging.getLogger(__name__)

HIVE_ENABLED_KEY = "hive_enabled"
HIVE_ONLY_KEY = "hive_only"
HIVE_PUBLIC_ADDRESS_KEY = "hive_public_address"
HIVE_INSTANCE_NAME_KEY = "hive_instance_name"
HIVE_NAME_DOCUMENT_KEY = "hive_name_document"
HIVE_NAME_SIGNATURE_KEY = "hive_name_signature"

# Clé acceptée une seule fois au PATCH pour le premier allumage (non persistée).
TRANSIENT_ACTIVATION_NAME_KEY = "hive_activation_name"

RESERVED_HIVE_KEYS = frozenset({HIVE_NAME_DOCUMENT_KEY, HIVE_NAME_SIGNATURE_KEY})

HIVE_SETTING_KEYS = frozenset(
    {
        HIVE_ENABLED_KEY,
        HIVE_PUBLIC_ADDRESS_KEY,
        HIVE_INSTANCE_NAME_KEY,
    }
)


def hive_data_dir(settings: Settings) -> Path:
    return Path(settings.data_dir) / "hive"


def hive_private_key_path(settings: Settings) -> Path:
    return hive_data_dir(settings) / "identity.key"


def _read_setting_text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return (row.value or "").strip() if row is not None else ""


def hive_enabled_in_db(db: Session) -> bool:
    return _read_setting_text(db, HIVE_ENABLED_KEY).lower() == "true"


def hive_only_in_db(db: Session) -> bool:
    return _read_setting_text(db, HIVE_ONLY_KEY).lower() == "true"


def _signed_identity_persisted(db: Session) -> bool:
    doc = _read_setting_text(db, HIVE_NAME_DOCUMENT_KEY)
    sig = _read_setting_text(db, HIVE_NAME_SIGNATURE_KEY)
    return bool(doc and sig)


def hive_identity_is_lost(db: Session, settings: Settings) -> bool:
    """Document signé en base mais aucune clé privée utilisable (fichier absent ou inaccessible)."""
    if not _signed_identity_persisted(db):
        return False
    return load_private_key(settings) is None


def load_private_key(settings: Settings):
    path = hive_private_key_path(settings)
    if not path.is_file():
        return None
    try:
        return private_key_from_pem(path.read_bytes())
    except (ValueError, OSError):
        logger.warning("Clé Hive illisible ou inaccessible (détail non journalisé).")
        return None


def write_private_key(settings: Settings, private_key) -> None:
    """Écrit la clé de façon atomique : jamais de fichier final lisible par d'autres avant 0600."""
    d = hive_data_dir(settings)
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    path = hive_private_key_path(settings)
    pem = private_key_to_pem(private_key)
    tmp_name = d / f".identity.key.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(tmp_name), flags, 0o600)
    try:
        f_obj = os.fdopen(fd, "wb")
    except Exception:
        os.close(fd)
        tmp_name.unlink(missing_ok=True)
        raise
    try:
        with f_obj as f:
            f.write(pem)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            tmp_name.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        os.replace(tmp_name, path)
    except OSError:
        try:
            tmp_name.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def delete_private_key_file(settings: Settings) -> None:
    path = hive_private_key_path(settings)
    if path.is_file():
        path.unlink()


def clear_hive_identity_rows(db: Session) -> None:
    db.execute(update(Media).where(Media.keep_source == "hive").values(keep=False, keep_source=None))
    db.execute(
        update(MediaItem).where(MediaItem.keep_source == "hive").values(keep=False, keep_source=None)
    )
    for key in (
        HIVE_INSTANCE_NAME_KEY,
        HIVE_NAME_DOCUMENT_KEY,
        HIVE_NAME_SIGNATURE_KEY,
        HIVE_PUBLIC_ADDRESS_KEY,
    ):
        row = db.get(Setting, key)
        if row is not None:
            row.value = ""
    db.execute(delete(HiveJoinPending))
    db.execute(delete(HiveInvitation))
    db.execute(delete(HiveMemberList))
    db.execute(delete(HiveMember))
    db.execute(delete(HiveRucheExcluded))
    db.execute(delete(HiveKnownIdentityName))
    db.execute(delete(HivePeerNonce))


def reset_hive_identity(db: Session, settings: Settings, *, new_name: str) -> None:
    """Nouvelle clé et nouveau nom ; vide l'appartenance aux ruches."""
    name = validate_instance_name(new_name.strip())

    delete_private_key_file(settings)
    clear_hive_identity_rows(db)
    db.flush()

    private_key, public_key = generate_keypair()
    write_private_key(settings, private_key)

    instance_id = hive_public_id_from_public_key(public_key)
    now = datetime.now(timezone.utc)
    document = build_name_document(instance_id=instance_id, name=name, signed_at=now)
    signature = sign_document(private_key, document)
    doc_json = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sig_b64 = base64.b64encode(signature).decode("ascii")

    verify_name_document(public_key, document, signature)

    for key, val in (
        (HIVE_INSTANCE_NAME_KEY, name),
        (HIVE_NAME_DOCUMENT_KEY, doc_json),
        (HIVE_NAME_SIGNATURE_KEY, sig_b64),
    ):
        row = db.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=val, is_secret=False, updated_at=now)
            db.add(row)
        else:
            row.value = val
            row.updated_at = now


def identity_state_dict(db: Session, settings: Settings) -> dict:
    """État pour GET /api/v1/hive/identity."""
    enabled = _read_setting_text(db, HIVE_ENABLED_KEY).lower() == "true"
    address = _read_setting_text(db, HIVE_PUBLIC_ADDRESS_KEY)
    name = _read_setting_text(db, HIVE_INSTANCE_NAME_KEY)
    private_key = load_private_key(settings)
    lost = hive_identity_is_lost(db, settings)
    if lost:
        doc_raw = _read_setting_text(db, HIVE_NAME_DOCUMENT_KEY)
        sig_raw = _read_setting_text(db, HIVE_NAME_SIGNATURE_KEY)
        doc = None
        if doc_raw:
            try:
                doc = json.loads(doc_raw)
            except json.JSONDecodeError:
                doc = None
        return {
            "enabled": enabled,
            "identity_lost": True,
            "identity_lost_message": (
                "Identité Hive perdue : la clé privée est absente ou illisible. "
                "Réinitialisez l’identité (nouveau nom) via POST /api/v1/hive/identity/reset."
            ),
            "instance_id": None,
            "public_key_pem": None,
            "name": name or None,
            "public_address": address or None,
            "pull_only": bool(enabled and not address),
            "name_document": doc,
            "name_signature_b64": sig_raw or None,
        }
    if private_key is None:
        return {
            "enabled": enabled,
            "identity_lost": False,
            "identity_lost_message": None,
            "instance_id": None,
            "public_key_pem": None,
            "name": name or None,
            "public_address": address or None,
            "pull_only": bool(enabled and not address),
            "name_document": None,
            "name_signature_b64": None,
        }
    public_key = private_key.public_key()
    instance_id = hive_public_id_from_public_key(public_key)
    doc_raw = _read_setting_text(db, HIVE_NAME_DOCUMENT_KEY)
    sig_raw = _read_setting_text(db, HIVE_NAME_SIGNATURE_KEY)
    doc = None
    if doc_raw:
        try:
            doc = json.loads(doc_raw)
        except json.JSONDecodeError:
            doc = None
    return {
        "enabled": enabled,
        "identity_lost": False,
        "identity_lost_message": None,
        "instance_id": instance_id,
        "public_key_pem": public_key_to_pem(public_key).decode("utf-8"),
        "name": name or None,
        "public_address": address or None,
        "pull_only": bool(enabled and not address),
        "name_document": doc,
        "name_signature_b64": sig_raw or None,
    }


def parse_bool_setting(v: object) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in {"true", "false", "1", "0"}:
        return v.strip().lower() in {"true", "1"}
    return None


def prepare_hive_settings_update(
    db: Session,
    settings: Settings,
    values: dict[str, object],
    *,
    activation_name: str | None,
) -> None:
    """Prépare l'activation : génère la clé et le document signé. Lève ValueError → SettingsError."""
    existing_enabled = _read_setting_text(db, HIVE_ENABLED_KEY).lower() == "true"
    existing_name = _read_setting_text(db, HIVE_INSTANCE_NAME_KEY)

    if HIVE_INSTANCE_NAME_KEY in values and isinstance(values[HIVE_INSTANCE_NAME_KEY], str):
        new_name = values[HIVE_INSTANCE_NAME_KEY].strip()
        if existing_name and new_name and new_name != existing_name:
            raise ValueError("Le nom d'instance Hive est définitif et ne peut pas être modifié.")
        if existing_name and not new_name:
            raise ValueError("Le nom d'instance Hive ne peut pas être effacé.")

    if HIVE_ENABLED_KEY not in values:
        if activation_name:
            raise ValueError("« hive_activation_name » n'est utilisé qu'à l'activation de Hive.")
        return

    want = parse_bool_setting(values[HIVE_ENABLED_KEY])
    if want is None:
        return
    if not want:
        return

    if existing_enabled:
        return

    if _signed_identity_persisted(db) and load_private_key(settings) is None:
        raise ValueError(
            "Identité Hive perdue : la clé privée est absente ou illisible. "
            "Réinitialisez l’identité via POST /api/v1/hive/identity/reset avant de rallumer Hive."
        )

    name_from_patch = (
        values[HIVE_INSTANCE_NAME_KEY].strip()
        if isinstance(values.get(HIVE_INSTANCE_NAME_KEY), str)
        else (existing_name or "")
    )
    name_candidate = (activation_name or "").strip() or name_from_patch
    if not name_candidate:
        raise ValueError("Un nom d'instance est requis pour la première activation de Hive.")
    name_final = validate_instance_name(name_candidate)

    private_key = load_private_key(settings)
    if private_key is None:
        private_key, public_key = generate_keypair()
        write_private_key(settings, private_key)
    else:
        public_key = private_key.public_key()

    instance_id = hive_public_id_from_public_key(public_key)
    now = datetime.now(timezone.utc)
    document = build_name_document(instance_id=instance_id, name=name_final, signed_at=now)
    signature = sign_document(private_key, document)
    doc_json = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sig_b64 = base64.b64encode(signature).decode("ascii")

    verify_name_document(public_key, document, signature)

    values[HIVE_INSTANCE_NAME_KEY] = name_final
    values[HIVE_NAME_DOCUMENT_KEY] = doc_json
    values[HIVE_NAME_SIGNATURE_KEY] = sig_b64
