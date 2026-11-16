"""Replace the live SQLite database from an on-disk backup."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import Session

from mediatracker.bootstrap import ensure_ready
from mediatracker.config import Settings
from mediatracker.db.migrate import run_migrations
from mediatracker.db.session import create_session_factory
from mediatracker.maintenance.backup import copy_sqlite_file, restore_sqlite_into_live, sqlite_path
from mediatracker.maintenance.backup_hive_identity import (
    BackupHiveIdentityError,
    apply_hive_identity_bundle,
    read_hive_identity_bundle,
    reapply_receiver_hive_after_import_on_disk,
    snapshot_hive_settings,
    strip_hive_identity_bundle_from_live,
)
from mediatracker.maintenance.restore_catalog import (
    BackupCatalogError,
    is_import_backup_name,
    preview_backup,
    resolve_backup_file,
    safety_root,
    schema_relation,
)
from mediatracker.maintenance.worker_gate import (
    enter_worker_maintenance,
    exit_worker_maintenance,
    wait_for_worker_maintenance_ack,
)

logger = logging.getLogger(__name__)


class RestoreError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class RestoreConflictError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class RestoreReport:
    backup: str
    safety_copy: str
    schema_revision: str | None
    detail: str


def restore_database(
    app: FastAPI,
    settings: Settings,
    db: Session,
    name: str,
    *,
    safety: bool = False,
    ack_timeout: float | None = None,
) -> RestoreReport:
    """Pause the worker, keep a safety copy, swap the database, migrate if needed."""
    try:
        backup_path = resolve_backup_file(settings, name, safety=safety)
    except BackupCatalogError as exc:
        raise RestoreError(exc.message) from exc

    live = sqlite_path(settings.database_url)
    if live is None or not live.is_file():
        raise RestoreError("La base active n'est pas un fichier SQLite.")

    preview = preview_backup(settings, name, current_fiches=0, safety=safety)
    relation = schema_relation(preview.schema_revision)
    if relation == "newer":
        raise RestoreError(
            "Le schéma de la sauvegarde est plus récent que cette version de l'application.",
        )

    is_import = not safety and is_import_backup_name(name)
    hive_settings = snapshot_hive_settings(db) if is_import else None

    enter_worker_maintenance(db)
    try:
        if not wait_for_worker_maintenance_ack(app.state.session_factory, timeout=ack_timeout):
            raise RestoreConflictError(
                "Le worker n'a pas confirmé la pause à temps. La base n'a pas été modifiée.",
            )
        safety_dir = safety_root(settings)
        moment = datetime.now(timezone.utc)
        safety_dir.mkdir(parents=True, exist_ok=True)
        safety_name = f"mediatracker-{moment.strftime('%Y%m%d')}-before-restore-{moment.strftime('%H%M%S')}.db"
        safety_path = safety_dir / safety_name
        copy_sqlite_file(live, safety_path)
        hive_bundle = None if is_import else _validated_hive_bundle(backup_path)
        _replace_live_database(
            app,
            settings,
            backup_path,
            live,
            hive_bundle,
            safety_path,
            hive_settings=hive_settings,
        )
    except RestoreError:
        raise
    except OSError as exc:
        logger.info("Restauration SQLite interrompue.")
        raise RestoreError("Restauration impossible : la base n'a pas été remplacée.") from exc
    finally:
        cleanup = app.state.session_factory()
        try:
            exit_worker_maintenance(cleanup, settings)
        finally:
            cleanup.close()

    return RestoreReport(
        backup=backup_path.name,
        safety_copy=safety_name,
        schema_revision=preview.schema_revision,
        detail="Base restaurée depuis la sauvegarde. Une copie de la base précédente est dans backups/safety.",
    )


def _validated_hive_bundle(backup_path: Path) -> bytes | None:
    try:
        return read_hive_identity_bundle(backup_path)
    except BackupHiveIdentityError as exc:
        raise RestoreError(exc.args[0]) from exc


def _replace_live_database(
    app: FastAPI,
    settings: Settings,
    backup_path: Path,
    live: Path,
    hive_bundle: bytes | None,
    safety_path: Path,
    *,
    hive_settings: dict[str, str] | None = None,
) -> None:
    app.state.engine.dispose()
    swapped = False
    try:
        restore_sqlite_into_live(backup_path, live)
        swapped = True
        strip_hive_identity_bundle_from_live(live)
        if hive_bundle is not None:
            apply_hive_identity_bundle(hive_bundle, settings)
        engine, factory = create_session_factory(settings)
        app.state.engine = engine
        app.state.session_factory = factory
        session = factory()
        try:
            enter_worker_maintenance(session)
            session.commit()
        finally:
            session.close()
        run_migrations(settings.database_url)
        app.state.engine.dispose()
        if hive_settings is not None:
            reapply_receiver_hive_after_import_on_disk(
                live,
                safety_path,
                hive_settings,
            )
        engine, factory = create_session_factory(settings)
        app.state.engine = engine
        app.state.session_factory = factory
        session = factory()
        try:
            ensure_ready(session, settings)
            session.commit()
        finally:
            session.close()
    except RestoreError:
        if swapped and safety_path.is_file():
            restore_sqlite_into_live(safety_path, live)
        raise
    except BackupHiveIdentityError as exc:
        if swapped and safety_path.is_file():
            restore_sqlite_into_live(safety_path, live)
        raise RestoreError(str(exc)) from exc
    except Exception as exc:
        if swapped and safety_path.is_file():
            restore_sqlite_into_live(safety_path, live)
        logger.info("Restauration interrompue après remplacement de la base.")
        raise RestoreError(
            "Restauration interrompue : la base active a été rétablie depuis la copie de sécurité.",
        ) from exc
