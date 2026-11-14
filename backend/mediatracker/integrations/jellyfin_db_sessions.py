"""Temporary on-disk storage for Jellyfin DB uploads during guided import."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import UploadFile

from mediatracker.api.errors import Problem
from mediatracker.integrations.jellyfin_efcore_data import (
    BACKUP_ZIP_NAME,
    JellyfinExportError,
    add_backup_uncompressed_bytes,
    is_backup_database_json_path,
    verify_sqlite_file,
)

SESSION_DIR_NAME = "jellyfin-history-sessions"
JELLYFIN_NAME = "jellyfin.db"
META_NAME = "session.json"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SESSION_EXPIRED_DETAIL = "Session d'import expirée, renvoyez le fichier."


@dataclass(frozen=True)
class DbSessionPaths:
    session_id: str
    root: Path
    jellyfin_db: Path | None
    backup_zip: Path | None
    created_at: datetime


def sessions_root(config_dir: Path) -> Path:
    return config_dir / SESSION_DIR_NAME


def _meta_path(root: Path) -> Path:
    return root / META_NAME


def _session_gone() -> Problem:
    return Problem(410, "Session d'import expirée", SESSION_EXPIRED_DETAIL)


def _validate_token(session_id: str) -> str:
    if not _TOKEN_RE.fullmatch(session_id):
        raise _session_gone()
    return session_id


def create_session(config_dir: Path) -> DbSessionPaths:
    session_id = secrets.token_urlsafe(24)
    root = sessions_root(config_dir) / session_id
    root.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    meta = {"created_at": now.isoformat(), "source": "sqlite"}
    _meta_path(root).write_text(json.dumps(meta), encoding="utf-8")
    return DbSessionPaths(
        session_id=session_id,
        root=root,
        jellyfin_db=root / JELLYFIN_NAME,
        backup_zip=None,
        created_at=now,
    )


def load_session(config_dir: Path, session_id: str) -> DbSessionPaths:
    session_id = _validate_token(session_id)
    root = sessions_root(config_dir) / session_id
    meta_file = _meta_path(root)
    if not meta_file.is_file():
        raise _session_gone()
    raw = json.loads(meta_file.read_text(encoding="utf-8"))
    created = datetime.fromisoformat(str(raw["created_at"]))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    source = str(raw.get("source") or "sqlite")
    jellyfin = root / JELLYFIN_NAME
    backup = root / BACKUP_ZIP_NAME
    if source == "backup_zip":
        if not backup.is_file():
            raise _session_gone()
        return DbSessionPaths(
            session_id=session_id,
            root=root,
            jellyfin_db=None,
            backup_zip=backup,
            created_at=created,
        )
    if not jellyfin.is_file():
        raise _session_gone()
    return DbSessionPaths(
        session_id=session_id,
        root=root,
        jellyfin_db=jellyfin,
        backup_zip=None,
        created_at=created,
    )


def destroy_session(config_dir: Path, session_id: str) -> None:
    session_id = _validate_token(session_id)
    root = sessions_root(config_dir) / session_id
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


async def stream_upload_to_path(upload: UploadFile, dest: Path, *, max_bytes: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with dest.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise Problem(
                        413,
                        "Fichier trop volumineux",
                        f"La base dépasse la taille maximale ({max_bytes // (1024 * 1024)} Mo).",
                    )
                handle.write(chunk)
    except Problem:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if total == 0:
        dest.unlink(missing_ok=True)
        raise Problem(422, "Requête invalide", "Fichier vide.")


async def stream_sqlite_upload(upload: UploadFile, dest: Path, *, max_bytes: int) -> None:
    await stream_upload_to_path(upload, dest, max_bytes=max_bytes)
    try:
        verify_sqlite_file(dest)
    except JellyfinExportError as exc:
        dest.unlink(missing_ok=True)
        raise Problem(422, "Requête invalide", str(exc)) from exc


def _validate_backup_zip(path: Path) -> None:
    total = 0
    found = False
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise JellyfinExportError("Archive ZIP invalide ou corrompue.") from exc
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if ".." in name.split("/"):
                raise JellyfinExportError("Archive refusée (zip-slip).")
            if not is_backup_database_json_path(name):
                continue
            found = True
            total = add_backup_uncompressed_bytes(total, info.file_size)
    if not found:
        raise JellyfinExportError("Archive Jellyfin invalide : aucun fichier Database/*.json.")


async def stream_backup_zip_upload(upload: UploadFile, dest: Path, *, max_bytes: int) -> None:
    await stream_upload_to_path(upload, dest, max_bytes=max_bytes)
    try:
        _validate_backup_zip(dest)
    except JellyfinExportError as exc:
        dest.unlink(missing_ok=True)
        raise Problem(422, "Requête invalide", str(exc)) from exc


def mark_backup_source(config_dir: Path, session_id: str) -> None:
    """Marque la session comme source ZIP sans exiger jellyfin.db (encore absent)."""
    session_id = _validate_token(session_id)
    root = sessions_root(config_dir) / session_id
    meta_file = _meta_path(root)
    if not meta_file.is_file():
        raise _session_gone()
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["source"] = "backup_zip"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")


def purge_stale_sessions(config_dir: Path, *, max_age_hours: int = 24) -> int:
    root = sessions_root(config_dir)
    if not root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta_file = _meta_path(child)
        if not meta_file.is_file():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            continue
        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(str(raw["created_at"]))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (json.JSONDecodeError, ValueError, TypeError):
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            continue
        if created < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
