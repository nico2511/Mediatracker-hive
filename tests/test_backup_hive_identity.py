"""Sauvegarde SQLite : inclusion et restauration de la clé Hive (hors base)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.conftest import csrf, login

from mediatracker.hive.identity import hive_identity_is_lost, hive_private_key_path
from mediatracker.maintenance.backup import backup_sqlite, backup_sqlite_force, copy_sqlite_file, sqlite_path
from mediatracker.maintenance.backup_hive_identity import (
    BackupHiveIdentityError,
    BackupHiveIdentityIncompleteError,
    read_hive_identity_bundle,
)
from mediatracker.maintenance.restore_catalog import backups_root
from mediatracker.maintenance.worker_gate import acknowledge_worker_maintenance, worker_maintenance_active


@pytest.fixture
def worker_ack_simulator(app):
    stop = threading.Event()

    def _poll() -> None:
        while not stop.is_set():
            db = app.state.session_factory()
            try:
                if worker_maintenance_active(db):
                    acknowledge_worker_maintenance(db)
                    db.commit()
            finally:
                db.close()
            stop.wait(0.02)

    thread = threading.Thread(target=_poll, daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=1)


def _enable_hive(client) -> str:
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": "bk",
                "hive_public_address": "https://mtr.exemple.fr",
            }
        },
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    body = client.get("/api/v1/hive/identity").json()
    assert body["identity_lost"] is False
    return body["instance_id"]


def _backup_with_hive(settings, moment: datetime) -> Path:
    dest = backups_root(settings)
    dest.mkdir(parents=True, exist_ok=True)
    source = sqlite_path(settings.database_url)
    assert source is not None
    path = backup_sqlite(source, dest, keep=9, now=moment, settings=settings)
    assert path is not None
    return path


def test_hive_identity_round_trip_backup_restore(client, app, settings, worker_ack_simulator):
    instance_id = _enable_hive(client)
    key_before = hive_private_key_path(settings).read_bytes()
    name = _backup_with_hive(settings, datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)).name

    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "After backup", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201

    hive_private_key_path(settings).unlink(missing_ok=True)

    done = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text

    body = client.get("/api/v1/hive/identity").json()
    assert body["identity_lost"] is False
    assert body["instance_id"] == instance_id
    key_after = hive_private_key_path(settings).read_bytes()
    assert key_after == key_before
    assert oct(hive_private_key_path(settings).stat().st_mode)[-3:] == "600"

    db = app.state.session_factory()
    try:
        assert hive_identity_is_lost(db, settings) is False
    finally:
        db.close()


def test_legacy_backup_without_bundle_keeps_existing_key(client, app, settings, worker_ack_simulator):
    instance_id = _enable_hive(client)
    key_before = hive_private_key_path(settings).read_bytes()
    source = sqlite_path(settings.database_url)
    assert source is not None
    dest = backups_root(settings)
    dest.mkdir(parents=True, exist_ok=True)
    legacy = dest / "mediatracker-20260101-120000.db"
    copy_sqlite_file(source, legacy)

    client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Noise", "year": 2019},
        headers=csrf(client),
    )

    done = client.post(
        f"/api/v1/maintenance/backups/{legacy.name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text
    assert hive_private_key_path(settings).read_bytes() == key_before
    assert client.get("/api/v1/hive/identity").json()["instance_id"] == instance_id


def test_corrupt_hive_bundle_refuses_restore(client, settings, worker_ack_simulator):
    _enable_hive(client)
    path = _backup_with_hive(settings, datetime(2026, 11, 7, 11, 0, tzinfo=timezone.utc))
    key_before = hive_private_key_path(settings).read_bytes()

    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE _mediatracker_backup_hive_identity SET key_sha256_hex = ? WHERE id = 1",
        ("0" * 64,),
    )
    conn.commit()
    conn.close()

    refused = client.post(
        f"/api/v1/maintenance/backups/{path.name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert refused.status_code == 422
    assert hive_private_key_path(settings).read_bytes() == key_before

    with pytest.raises(BackupHiveIdentityError):
        read_hive_identity_bundle(path)


def test_backup_file_mode_0600_under_permissive_umask(client, settings, monkeypatch):
    import os

    _enable_hive(client)
    source = sqlite_path(settings.database_url)
    assert source is not None
    monkeypatch.setattr(os, "umask", lambda: 0o000)
    path = backup_sqlite_force(
        source,
        backups_root(settings),
        settings=settings,
    )
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_backup_refuses_signed_identity_without_private_key(client, settings):
    _enable_hive(client)
    source = sqlite_path(settings.database_url)
    assert source is not None
    hive_private_key_path(settings).unlink(missing_ok=True)
    with pytest.raises(BackupHiveIdentityIncompleteError):
        backup_sqlite_force(source, backups_root(settings), settings=settings)


def test_restore_rolls_back_live_db_when_key_write_fails(
    client, app, settings, worker_ack_simulator, monkeypatch,
):
    instance_id = _enable_hive(client)
    client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Witness row", "year": 2021},
        headers=csrf(client),
    )
    name = _backup_with_hive(settings, datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)).name
    client.post(
        "/api/v1/media",
        json={"type": "film", "title": "After backup", "year": 2022},
        headers=csrf(client),
    )

    def _boom(_settings, _bundle):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(
        "mediatracker.maintenance.restore.apply_hive_identity_bundle",
        _boom,
    )
    refused = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert refused.status_code == 422
    listings = client.get("/api/v1/media").json()
    titles = {row["title"] for row in listings["items"]}
    assert "Witness row" in titles
    assert "After backup" in titles
    assert client.get("/api/v1/hive/identity").json()["instance_id"] == instance_id


def test_embed_only_on_backup_file_not_live(client, settings):
    from mediatracker.maintenance.backup import backup_sqlite_force
    _enable_hive(client)
    source = sqlite_path(settings.database_url)
    assert source is not None
    path = backup_sqlite_force(source, backups_root(settings), settings=settings)
    assert read_hive_identity_bundle(path) is not None
    conn = sqlite3.connect(source)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_mediatracker_backup_hive_identity'",
        ).fetchone()
        assert row is None
    finally:
        conn.close()
