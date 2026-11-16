"""Validate and store an uploaded SQLite backup under the backups catalog."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from mediatracker.config import Settings
from mediatracker.maintenance.backup import _secure_backup_file_mode
from mediatracker.maintenance.restore_catalog import (
    BackupSummary,
    allocate_import_filename,
    backups_root,
)

SQLITE_MAGIC = b"SQLite format 3\x00"
BACKUP_IMPORT_MAX_BYTES = 512 * 1024 * 1024
_READ_CHUNK = 1024 * 1024
_BUSY_MS = 30_000


class BackupImportError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


async def import_backup_upload(
    upload: UploadFile,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> BackupSummary:
    """Stream upload to a catalog name ``import-AAAAMMJJ-HHMMSS.db`` after validation."""
    moment = now or datetime.now(timezone.utc)
    root = backups_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    temp = root / f".import-{secrets.token_hex(8)}.tmp"
    spooled = upload.file
    if spooled is None:
        raise BackupImportError("Fichier vide.")
    try:
        return await run_in_threadpool(
            _stream_validate_and_finalize,
            spooled,
            temp,
            root,
            moment,
        )
    except BackupImportError:
        temp.unlink(missing_ok=True)
        raise
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _stream_validate_and_finalize(
    source: BinaryIO,
    temp: Path,
    root: Path,
    moment: datetime,
) -> BackupSummary:
    source.seek(0)
    total = 0
    with temp.open("wb") as handle:
        while True:
            chunk = source.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > BACKUP_IMPORT_MAX_BYTES:
                raise BackupImportError(
                    f"Fichier trop volumineux (max. {BACKUP_IMPORT_MAX_BYTES // (1024 * 1024)} Mo).",
                )
            handle.write(chunk)
    if total == 0:
        raise BackupImportError("Fichier vide.")
    validate_backup_import_file(temp)
    final_name = allocate_import_filename(root, moment)
    dest = root / final_name
    temp.replace(dest)
    _secure_backup_file_mode(dest)
    size = dest.stat().st_size
    return BackupSummary(name=final_name, created_at=moment, size_bytes=size)


def validate_backup_import_file(path: Path) -> None:
    if not path.is_file():
        raise BackupImportError("Fichier introuvable.")
    with path.open("rb") as handle:
        if handle.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
            raise BackupImportError("Le fichier n'est pas une base SQLite valide.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute(f"pragma busy_timeout={_BUSY_MS}")
    try:
        quick = conn.execute("pragma quick_check").fetchone()
        if not quick or str(quick[0]).lower() != "ok":
            integrity = conn.execute("pragma integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise BackupImportError("La base SQLite est corrompue ou illisible.")
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'",
        ).fetchone()
        if not table:
            raise BackupImportError("Ce fichier n'est pas une base MediaTracker (schéma absent).")
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        if not row or not row[0]:
            raise BackupImportError("Ce fichier n'est pas une base MediaTracker (schéma absent).")
    finally:
        conn.close()