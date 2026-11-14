"""Backup list, preview, and restore API."""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.conftest import csrf, login

from mediatracker.db.models import Media
from mediatracker.maintenance.backup import backup_sqlite, copy_sqlite_file, sqlite_path
from mediatracker.maintenance.restore_catalog import (
    BackupCatalogError,
    backups_root,
    resolve_backup_file,
    safety_root,
)
from mediatracker.maintenance.worker_gate import acknowledge_worker_maintenance, worker_maintenance_active


@pytest.fixture
def worker_ack_simulator(app):
    """Mimic the s6 worker acknowledging maintenance pause."""

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


def _bearer(client, name: str = "restore") -> dict[str, str]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": name, "scopes": ["write"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def _seed_fiche(client, label: str) -> None:
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": label, "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text


def _backup_name(settings, moment: datetime) -> str:
    dest = backups_root(settings)
    dest.mkdir(parents=True, exist_ok=True)
    path = backup_sqlite(sqlite_path(settings.database_url), dest, keep=9, now=moment, settings=settings)
    assert path is not None
    return path.name


def test_backup_preview_reports_counts_and_delta(client, app, settings):
    assert client.get("/api/v1/maintenance/backups").status_code == 401
    login(client)
    _seed_fiche(client, "Slot A")
    name = _backup_name(settings, datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc))
    _seed_fiche(client, "Slot B")

    preview = client.get(f"/api/v1/maintenance/backups/{name}/preview", headers=csrf(client))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["name"] == name
    assert body["fiches"] == 1
    assert body["fiches_delta"] == -1
    assert body["schema_revision"]
    assert body["app_version"] == "2.0.0"
    assert body["restorable"] is True
    assert body["schema_relation"] in {"same", "older", "unknown"}


def test_backup_preview_rejects_path_outside_backups_dir(client, settings):
    login(client)
    with pytest.raises(BackupCatalogError):
        resolve_backup_file(settings, "../app.db")
    with pytest.raises(BackupCatalogError):
        resolve_backup_file(settings, "subdir/evil.db")
    bad = client.get("/api/v1/maintenance/backups/not-a-backup.db/preview", headers=csrf(client))
    assert bad.status_code == 404


def test_restore_replaces_data_and_keeps_safety_copy(client, app, settings, worker_ack_simulator):
    login(client)
    _seed_fiche(client, "Slot A")
    name = _backup_name(settings, datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc))
    _seed_fiche(client, "Slot B")

    refused = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "NOPE"},
        headers=csrf(client),
    )
    assert refused.status_code == 422

    done = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text
    payload = done.json()
    safety = safety_root(settings) / payload["safety_copy"]
    assert safety.is_file()

    db = app.state.session_factory()
    try:
        titles = sorted(row.title for row in db.query(Media).all())
        assert titles == ["Slot A"]
    finally:
        db.close()


def test_backup_preview_marks_newer_schema_not_restorable(client, app, settings):
    login(client)
    _seed_fiche(client, "Slot C")
    name = _backup_name(settings, datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    backup = backups_root(settings) / name
    conn = sqlite3.connect(backup)
    try:
        conn.execute("update alembic_version set version_num = ?", ("9999_future",))
        conn.commit()
    finally:
        conn.close()

    preview = client.get(f"/api/v1/maintenance/backups/{name}/preview", headers=csrf(client))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["restorable"] is False
    assert body["schema_relation"] == "newer"


def test_restore_refuses_newer_schema(client, app, settings):
    login(client)
    _seed_fiche(client, "Slot C")
    name = _backup_name(settings, datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    backup = backups_root(settings) / name
    conn = sqlite3.connect(backup)
    try:
        conn.execute("update alembic_version set version_num = ?", ("9999_future",))
        conn.commit()
    finally:
        conn.close()

    blocked = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert blocked.status_code == 422, blocked.text


def test_backup_endpoints_accept_write_bearer(client, settings):
    login(client)
    name = _backup_name(settings, datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc))
    headers = _bearer(client)
    listed = client.get("/api/v1/maintenance/backups", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(row["name"] == name for row in listed.json()["items"])
    preview = client.get(f"/api/v1/maintenance/backups/{name}/preview", headers=headers)
    assert preview.status_code == 200, preview.text


def test_restore_without_worker_ack_returns_409_and_keeps_data(client, app, settings, monkeypatch):
    login(client)
    _seed_fiche(client, "Slot A")
    name = _backup_name(settings, datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc))
    _seed_fiche(client, "Slot B")
    monkeypatch.setattr(
        "mediatracker.maintenance.restore.wait_for_worker_maintenance_ack",
        lambda *args, **kwargs: False,
    )
    blocked = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert blocked.status_code == 409, blocked.text
    db = app.state.session_factory()
    try:
        titles = sorted(row.title for row in db.query(Media).all())
        assert titles == ["Slot A", "Slot B"]
    finally:
        db.close()


def test_restore_does_not_delete_wal_or_shm_files(client, app, settings, worker_ack_simulator, monkeypatch):
    login(client)
    _seed_fiche(client, "Slot A")
    name = _backup_name(settings, datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc))
    live = sqlite_path(settings.database_url)
    assert live is not None
    wal = Path(f"{live}-wal")
    shm = Path(f"{live}-shm")
    wal.write_bytes(b"wal-chunk")
    shm.write_bytes(b"shm-chunk")
    unlinked: list[str] = []
    real_unlink = Path.unlink

    def _track_unlink(self, *args, **kwargs):
        unlinked.append(self.name)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _track_unlink)

    done = client.post(
        f"/api/v1/maintenance/backups/{name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text
    assert not any(item.endswith("-wal") or item.endswith("-shm") for item in unlinked)


def test_safety_backup_list_and_restore_path_guards(client, settings, worker_ack_simulator):
    login(client)
    name = _backup_name(settings, datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc))
    safety_dir = safety_root(settings)
    safety_dir.mkdir(parents=True, exist_ok=True)
    safety_name = "mediatracker-20261027-before-restore-120000.db"
    copy_sqlite_file(backups_root(settings) / name, safety_dir / safety_name)

    listed = client.get("/api/v1/maintenance/backups/safety", headers=csrf(client))
    assert listed.status_code == 200, listed.text
    names = [row["name"] for row in listed.json()["items"]]
    assert safety_name in names

    with pytest.raises(BackupCatalogError):
        resolve_backup_file(settings, "../app.db", safety=True)
    bad = client.get("/api/v1/maintenance/backups/safety/not-a-backup.db/preview", headers=csrf(client))
    assert bad.status_code == 404

    _seed_fiche(client, "After safety list")
    done = client.post(
        f"/api/v1/maintenance/backups/safety/{safety_name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text


def test_restore_reads_backup_in_read_only_mode(tmp_path, monkeypatch):
    source = tmp_path / "live.db"
    dest = tmp_path / "copy.db"
    conn = sqlite3.connect(source)
    conn.execute("create table note (id integer primary key, body text)")
    conn.execute("insert into note (body) values ('ok')")
    conn.commit()
    conn.close()

    calls: list[str] = []
    real_connect = sqlite3.connect

    def _spy_connect(target, *args, **kwargs):
        calls.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)
    from mediatracker.maintenance.restore_catalog import _read_backup_meta

    _read_backup_meta(source)
    assert any("mode=ro" in item for item in calls)
    copy_sqlite_file(source, dest)
    copied = sqlite3.connect(dest)
    try:
        assert copied.execute("select body from note").fetchone()[0] == "ok"
    finally:
        copied.close()
