"""Téléchargement et import de sauvegardes SQLite."""

from __future__ import annotations

import io
import sqlite3
import threading
from datetime import datetime, timezone

import pytest
from tests.conftest import csrf, login

from mediatracker.db.models import HiveMember, HiveRuche
from mediatracker.hive.identity import HIVE_INSTANCE_NAME_KEY, HIVE_NAME_DOCUMENT_KEY, hive_private_key_path
from mediatracker.maintenance.backup import backup_sqlite, copy_sqlite_file, sqlite_path
from mediatracker.maintenance.backup_import import (
    BACKUP_IMPORT_MAX_BYTES,
    BackupImportError,
    validate_backup_import_file,
)
from mediatracker.maintenance.restore_catalog import (
    BackupCatalogError,
    allocate_import_filename,
    backups_root,
    resolve_backup_file,
)
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


def _bearer_write(client) -> dict[str, str]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": "bk-dl", "scopes": ["write"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def _backup_name(settings, moment: datetime) -> str:
    dest = backups_root(settings)
    dest.mkdir(parents=True, exist_ok=True)
    source = sqlite_path(settings.database_url)
    assert source is not None
    path = backup_sqlite(source, dest, keep=9, now=moment, settings=settings)
    assert path is not None
    return path.name


def _enable_hive(client) -> str:
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": "imp",
                "hive_public_address": "https://mtr.exemple.fr",
            }
        },
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    body = client.get("/api/v1/hive/identity").json()
    return body["instance_id"]


def test_download_backup_ok(client, settings):
    login(client)
    name = _backup_name(settings, datetime(2026, 11, 14, 6, 12, tzinfo=timezone.utc))
    response = client.get(f"/api/v1/maintenance/backups/{name}/download", headers=csrf(client))
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/octet-stream")
    assert response.headers.get("cache-control") == "no-store"
    assert "attachment" in (response.headers.get("content-disposition") or "").lower()
    assert response.content[:16] == b"SQLite format 3\x00"


def test_download_backup_404_and_traversal(client, settings):
    login(client)
    missing = client.get("/api/v1/maintenance/backups/not-a-backup.db/download", headers=csrf(client))
    assert missing.status_code == 404

    for bad_name in ("../app.db", "subdir/evil.db", "app.db", ".."):
        with pytest.raises(BackupCatalogError):
            resolve_backup_file(settings, bad_name)

    live_db = sqlite_path(settings.database_url)
    assert live_db is not None
    live_header = live_db.read_bytes()[:16]
    sqlite_magic = b"SQLite format 3\x00"

    app_db = client.get("/api/v1/maintenance/backups/app.db/download", headers=csrf(client))
    assert app_db.status_code == 404
    assert app_db.content[:16] != live_header
    assert app_db.content[:16] != sqlite_magic

    # %2e%2e%2f est normalisé côté client HTTP et peut tomber sur le fallback SPA (200 HTML).
    # Un seul segment avec « .. » atteint la route API ; le resolver doit refuser.
    dotted = client.get("/api/v1/maintenance/backups/..%2Fapp.db/download", headers=csrf(client))
    assert dotted.status_code in (404, 200)
    if dotted.status_code == 200:
        assert dotted.headers.get("content-type", "").startswith("text/html")
    assert dotted.content[:16] != live_header
    assert dotted.content[:16] != sqlite_magic


def test_download_requires_admin_scope(client, settings):
    login(client)
    name = _backup_name(settings, datetime(2026, 11, 14, 7, 0, tzinfo=timezone.utc))
    blocked = client.get(f"/api/v1/maintenance/backups/{name}/download", headers=_bearer_write(client))
    assert blocked.status_code == 403


def test_safety_download_ok(client, settings):
    login(client)
    name = _backup_name(settings, datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc))
    safety_dir = backups_root(settings) / "safety"
    safety_dir.mkdir(parents=True, exist_ok=True)
    safety_name = "mediatracker-20261114-before-restore-080001.db"
    copy_sqlite_file(backups_root(settings) / name, safety_dir / safety_name)
    response = client.get(
        f"/api/v1/maintenance/backups/safety/{safety_name}/download",
        headers=csrf(client),
    )
    assert response.status_code == 200
    assert response.content[:16] == b"SQLite format 3\x00"


def test_allocate_import_filename_collision(tmp_path):
    moment = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    (tmp_path / "import-20261114-120000.db").write_bytes(b"x")
    assert allocate_import_filename(tmp_path, moment) == "import-20261114-120000-2.db"
    (tmp_path / "import-20261114-120000-2.db").write_bytes(b"y")
    assert allocate_import_filename(tmp_path, moment) == "import-20261114-120000-3.db"


def test_import_uses_threadpool_for_disk_and_validation(client, settings, monkeypatch):
    login(client)
    observed: list[str] = []

    async def _track(fn, *args, **kwargs):
        observed.append(getattr(fn, "__name__", "callable"))
        return fn(*args, **kwargs)

    monkeypatch.setattr("mediatracker.maintenance.backup_import.run_in_threadpool", _track)
    name = _backup_name(settings, datetime(2026, 11, 14, 11, 30, tzinfo=timezone.utc))
    payload = (backups_root(settings) / name).read_bytes()
    response = client.post(
        "/api/v1/maintenance/backups/import",
        files={"file": ("pick.db", io.BytesIO(payload), "application/octet-stream")},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    assert observed == ["_stream_validate_and_finalize"]


def test_import_valid_file(client, settings):
    login(client)
    name = _backup_name(settings, datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc))
    payload = (backups_root(settings) / name).read_bytes()
    response = client.post(
        "/api/v1/maintenance/backups/import",
        files={"file": ("foreign.db", io.BytesIO(payload), "application/octet-stream")},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"].startswith("import-")
    assert (backups_root(settings) / body["name"]).is_file()
    listed = client.get("/api/v1/maintenance/backups", headers=csrf(client)).json()["items"]
    assert any(row["name"] == body["name"] for row in listed)


def test_import_rejects_invalid_sqlite(client):
    login(client)
    response = client.post(
        "/api/v1/maintenance/backups/import",
        files={"file": ("bad.db", io.BytesIO(b"not-sqlite"), "application/octet-stream")},
        headers=csrf(client),
    )
    assert response.status_code == 422
    assert "sqlite" in response.json()["detail"].lower()


def test_import_rejects_missing_alembic(client, tmp_path):
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    conn.execute("create table note (id integer)")
    conn.commit()
    conn.close()
    with pytest.raises(BackupImportError):
        validate_backup_import_file(path)


def test_import_rejects_oversize(client, settings, monkeypatch):
    login(client)
    name = _backup_name(settings, datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(
        "mediatracker.maintenance.backup_import.BACKUP_IMPORT_MAX_BYTES",
        64,
    )
    payload = (backups_root(settings) / name).read_bytes()
    response = client.post(
        "/api/v1/maintenance/backups/import",
        files={"file": ("huge.db", io.BytesIO(payload), "application/octet-stream")},
        headers=csrf(client),
    )
    assert response.status_code == 413
    assert BACKUP_IMPORT_MAX_BYTES == 512 * 1024 * 1024


def test_import_restore_keeps_hive_identity(client, app, settings, worker_ack_simulator):
    instance_id = _enable_hive(client)
    key_before = hive_private_key_path(settings).read_bytes()
    source = sqlite_path(settings.database_url)
    assert source is not None
    import_name = "import-20261114-120000.db"
    dest = backups_root(settings)
    dest.mkdir(parents=True, exist_ok=True)
    import_path = dest / import_name
    copy_sqlite_file(source, import_path)
    conn = sqlite3.connect(import_path)
    try:
        conn.execute(
            "UPDATE setting SET value = ? WHERE key = ?",
            ('{"foreign": true}', HIVE_NAME_DOCUMENT_KEY),
        )
        conn.execute(
            "UPDATE setting SET value = ? WHERE key = ?",
            ("foreign-name", HIVE_INSTANCE_NAME_KEY),
        )
        conn.commit()
    finally:
        conn.close()

    client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Noise", "year": 2020},
        headers=csrf(client),
    )

    done = client.post(
        f"/api/v1/maintenance/backups/{import_name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text
    body = client.get("/api/v1/hive/identity").json()
    assert body["instance_id"] == instance_id
    assert hive_private_key_path(settings).read_bytes() == key_before


def test_import_restore_keeps_hive_ruches_and_members(client, app, settings, worker_ack_simulator):
    _enable_hive(client)
    created = client.post("/api/v1/hive/ruches", headers=csrf(client))
    assert created.status_code == 201, created.text
    ruche_hex = created.json()["ruche_id_hex"]

    db = app.state.session_factory()
    try:
        members_before = db.query(HiveMember).count()
        ruches_before = db.query(HiveRuche).count()
        assert ruches_before >= 1
        assert members_before >= 1
    finally:
        db.close()

    source = sqlite_path(settings.database_url)
    assert source is not None
    dest = backups_root(settings)
    dest.mkdir(parents=True, exist_ok=True)
    import_name = "import-20261114-120002.db"
    import_path = dest / import_name
    copy_sqlite_file(source, import_path)
    conn = sqlite3.connect(import_path)
    try:
        for (table_name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'hive_*'",
        ).fetchall():
            conn.execute(f'DELETE FROM "{table_name}"')
        conn.execute(
            "INSERT INTO hive_ruche (ruche_id, creator_instance_id, created_at) VALUES (?, ?, datetime('now'))",
            (b"\xff" * 16, "foreign-instance"),
        )
        conn.commit()
    finally:
        conn.close()

    done = client.post(
        f"/api/v1/maintenance/backups/{import_name}/restore",
        json={"confirm": "RESTORE"},
        headers=csrf(client),
    )
    assert done.status_code == 200, done.text

    db = app.state.session_factory()
    try:
        assert db.query(HiveRuche).count() == ruches_before
        assert db.query(HiveMember).count() == members_before
        assert db.get(HiveRuche, bytes.fromhex(ruche_hex)) is not None
        assert db.get(HiveRuche, b"\xff" * 16) is None
    finally:
        db.close()