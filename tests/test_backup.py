"""SQLite backup: online consistency, settings, rotation, and cache exclusion."""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mediatracker.db.models import Setting
from mediatracker.maintenance.backup import backup_sqlite, backup_sqlite_force, maybe_backup


def test_daily_backup_rotates(tmp_path):
    source = tmp_path / "app.db"
    conn = sqlite3.connect(source)
    conn.execute("create table note (id integer primary key, body text)")
    conn.execute("insert into note (body) values ('bonjour')")
    conn.commit()
    conn.close()
    dest = tmp_path / "backups"
    start = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    first = backup_sqlite(source, dest, keep=3, now=start)
    assert first is not None and first.is_file()
    assert backup_sqlite(source, dest, keep=3, now=start + timedelta(hours=2)) is None
    assert len(list(dest.glob("mediatracker-*.db"))) == 1
    copied = sqlite3.connect(first)
    try:
        assert copied.execute("select body from note").fetchone()[0] == "bonjour"
    finally:
        copied.close()
    for offset in range(1, 5):
        backup_sqlite(source, dest, keep=3, now=start + timedelta(days=offset))
    names = sorted(path.name for path in dest.glob("mediatracker-*.db"))
    assert names == [
        "mediatracker-20261003-030000.db",
        "mediatracker-20261004-030000.db",
        "mediatracker-20261005-030000.db",
    ]


def _seed_writable_db(source: Path) -> None:
    conn = sqlite3.connect(source)
    conn.execute("pragma journal_mode=wal")
    conn.execute(
        "create table setting ("
        "key text primary key, value text, is_secret integer not null default 0, updated_at text)"
    )
    conn.execute(
        "insert into setting (key, value, is_secret, updated_at) values (?, ?, 0, ?)",
        ("backup_keep", "9", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute("create table counter (id integer primary key autoincrement)")
    conn.commit()
    conn.close()


def test_backup_integrity_during_concurrent_writes(tmp_path):
    source = tmp_path / "app.db"
    _seed_writable_db(source)
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        conn = sqlite3.connect(source, timeout=30)
        try:
            conn.execute("pragma journal_mode=wal")
            while not stop.is_set():
                conn.execute("insert into counter default values")
                conn.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    time.sleep(0.05)
    dest_dir = tmp_path / "backups"
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    backup_path = backup_sqlite(source, dest_dir, keep=3, now=moment)
    stop.set()
    thread.join(timeout=5)
    assert not errors
    assert backup_path is not None and backup_path.is_file()

    backup = sqlite3.connect(backup_path)
    try:
        assert backup.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert backup.execute("select value from setting where key='backup_keep'").fetchone()[0] == "9"
        assert backup.execute("select count(*) from counter").fetchone()[0] > 0
    finally:
        backup.close()


def test_backup_includes_settings_table(tmp_path):
    source = tmp_path / "app.db"
    _seed_writable_db(source)
    dest_dir = tmp_path / "backups"
    path = backup_sqlite(source, dest_dir, keep=3)
    assert path is not None
    backup = sqlite3.connect(path)
    try:
        rows = backup.execute("select key, value from setting order by key").fetchall()
        assert rows == [("backup_keep", "9")]
    finally:
        backup.close()


def test_second_automatic_backup_same_day_is_skipped(tmp_path):
    source = tmp_path / "app.db"
    _seed_writable_db(source)
    dest_dir = tmp_path / "backups"
    start = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    first = backup_sqlite(source, dest_dir, keep=5, now=start)
    assert first is not None
    second = backup_sqlite(source, dest_dir, keep=5, now=start + timedelta(hours=6))
    assert second is None
    assert len(list(dest_dir.glob("mediatracker-20261101-*.db"))) == 1


def test_manual_backup_allowed_same_day(tmp_path):
    source = tmp_path / "app.db"
    _seed_writable_db(source)
    dest_dir = tmp_path / "backups"
    start = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    backup_sqlite(source, dest_dir, keep=5, now=start)
    forced = backup_sqlite_force(source, dest_dir, now=start + timedelta(minutes=5))
    assert forced.is_file()
    assert "reset" in forced.name
    assert len(list(dest_dir.glob("mediatracker-20261102-*.db"))) == 2


def test_image_cache_not_copied_into_backups(tmp_path):
    source = tmp_path / "config" / "app.db"
    source.parent.mkdir(parents=True)
    _seed_writable_db(source)
    cache_dir = tmp_path / "config" / "cache" / "images"
    cache_dir.mkdir(parents=True)
    poster = cache_dir / "poster-sample.webp"
    poster.write_bytes(b"\x00\x01\x02")

    dest_dir = tmp_path / "config" / "backups"
    path = backup_sqlite(source, dest_dir, keep=3)
    assert path is not None
    assert poster.is_file()
    assert list(dest_dir.glob("*.webp")) == []
    assert list(dest_dir.glob("mediatracker-*.db")) == [path]


def test_maybe_backup_reads_backup_keep_from_settings(client, app):
    settings = app.state.settings
    db = app.state.session_factory()
    try:
        row = db.get(Setting, "backup_keep")
        assert row is not None
        row.value = "2"
        db.commit()
        start = datetime(2026, 12, 1, 4, 0, tzinfo=timezone.utc)
        dest_dir = Path(settings.config_dir) / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for offset in range(4):
            maybe_backup(settings, db, now=start + timedelta(days=offset))
        names = sorted(path.name for path in dest_dir.glob("mediatracker-*.db"))
        assert len(names) == 2
    finally:
        db.close()
