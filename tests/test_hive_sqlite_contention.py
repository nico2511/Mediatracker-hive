"""Régression Hive : contention SQLite fichier réel, pas de cascade PendingRollback."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.db.models import Base, HiveFileFingerprint, LibraryFile, Media, MediaItem, Setting
from mediatracker.db.session import SQLITE_BUSY_MS, apply_sqlite_pragmas, is_sqlite_locked
from mediatracker.hive import fingerprint_daemon as daemon_mod
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.worker import _maybe_savepoint

NOW = datetime(2026, 11, 8, 18, 19, tzinfo=timezone.utc)


def _file_db(tmp_path: Path) -> tuple[sessionmaker[Session], Path]:
    db_path = tmp_path / "contention.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    event.listen(engine, "connect", apply_sqlite_pragmas)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    return factory, db_path


def _seed_media_items(db: Session, count: int, library: Path) -> list[int]:
    library.mkdir(parents=True, exist_ok=True)
    ids: list[int] = []
    media = Media(type="series", title="Serie demo", year=2000, tmdb_id=900001)
    db.add(media)
    db.flush()
    for i in range(count):
        rel = library / f"e{i:03d}.mkv"
        rel.write_bytes(b"x")
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=i + 1,
            file_path=str(rel),
            downloaded=True,
        )
        db.add(item)
        db.flush()
        grant_share_intent(db, item.id)
        db.add(
            LibraryFile(
                path=str(rel),
                size=1,
                parsed_title="Serie demo",
                parsed_year=2000,
                season=1,
                episode=i + 1,
                tmdb_id=900001,
                media_type="series",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        ids.append(item.id)
    db.commit()
    return ids


def _minimal_settings(library: Path) -> Settings:
    return Settings(
        secret_key="test-secret-key-0123456789",
        data_dir=library.parent / "data",
        config_dir=library.parent / "cfg",
        database_url="sqlite:///:memory:",
    )


def test_ensure_fingerprint_skips_redundant_updated_at_touch(tmp_path):
    """Sans changement fichier, sync ne doit pas marquer toutes les lignes dirty."""
    library = tmp_path / "lib"
    factory, _ = _file_db(tmp_path)
    settings = _minimal_settings(library)
    db = factory()
    try:
        ids = _seed_media_items(db, 12, library)
        for item_id in ids:
            item = db.get(MediaItem, item_id)
            assert item is not None
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()

        for item_id in ids:
            item = db.get(MediaItem, item_id)
            assert item is not None
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW + timedelta(seconds=1))
        assert not db.dirty and not db.new
    finally:
        db.close()


def test_savepoint_lock_inside_nested_preserves_outer_writes(tmp_path):
    """Verrou dans le SAVEPOINT : écritures hors savepoint conservées au commit."""
    factory, _ = _file_db(tmp_path)
    db = factory()
    try:
        db.add(Setting(key="worker_keep", value="1", is_secret=False, updated_at=NOW))

        def locked_inside() -> None:
            db.add(Setting(key="inner_lost", value="x", is_secret=False, updated_at=NOW))
            db.flush()
            raise OperationalError(
                "UPDATE setting",
                {},
                sqlite3.OperationalError("database is locked"),
            )

        marker = {"next_step": False}

        def next_step() -> None:
            marker["next_step"] = True

        _maybe_savepoint(db, "Hive gossip", locked_inside)
        _maybe_savepoint(db, "Hive gossip signal", next_step)
        assert marker["next_step"] is True
        db.commit()
    finally:
        db.close()

    check = factory()
    try:
        assert check.get(Setting, "worker_keep") is not None
        assert check.get(Setting, "inner_lost") is None
    finally:
        check.close()


def test_maybe_savepoint_recovers_after_lock_during_nested_flush(tmp_path):
    """Verrou au flush après beaucoup de lignes dirty : rollback puis étape suivante OK."""
    library = tmp_path / "lib"
    factory, db_path = _file_db(tmp_path)
    settings = _minimal_settings(library)
    seed = factory()
    try:
        ids = _seed_media_items(seed, 80, library)
        for item_id in ids:
            item = seed.get(MediaItem, item_id)
            assert item is not None
            ensure_fingerprint_row_for_item(seed, settings, item, now=NOW)
        seed.commit()
    finally:
        seed.close()

    block = threading.Event()
    released = threading.Event()

    def holder():
        conn = sqlite3.connect(db_path, timeout=60)
        conn.execute("BEGIN IMMEDIATE")
        block.set()
        released.wait(timeout=30)
        conn.commit()
        conn.close()

    holder_thread = threading.Thread(target=holder, daemon=True)
    holder_thread.start()
    assert block.wait(timeout=5)

    worker = factory()
    try:
        later = NOW + timedelta(seconds=5)
        for item_id in ids:
            row = worker.get(HiveFileFingerprint, item_id)
            assert row is not None
            # Reproduit l'ancien lot de UPDATE updated_at (type NAS ~228 lignes).
            row.updated_at = later

        marker = {"gossip_ok": False}

        def gossip_step() -> None:
            worker.get(Setting, "hive_gossip_next_at")

        def burst_step() -> None:
            marker["gossip_ok"] = True
            worker.get(Setting, "hive_gossip_burst_state")

        _maybe_savepoint(worker, "Hive gossip", gossip_step)
        _maybe_savepoint(worker, "Hive gossip signal", burst_step)
        assert marker["gossip_ok"] is True
        worker.rollback()
    finally:
        released.set()
        holder_thread.join(timeout=10)
        worker.close()

    cycle2 = factory()
    try:
        cycle2.add(Setting(key="worker_cycle2", value="ok", is_secret=False, updated_at=NOW))

        def purge_step() -> None:
            cycle2.get(Setting, "worker_cycle2")

        _maybe_savepoint(cycle2, "Purge événements", purge_step)
        cycle2.commit()
    finally:
        cycle2.close()

    verify = factory()
    try:
        assert verify.get(Setting, "worker_cycle2") is not None
    finally:
        verify.close()


def test_concurrent_writer_with_fingerprint_tick_real_file(tmp_path):
    """Lecture hachage daemon + autre writer sur fichier SQLite WAL (pas :memory:)."""
    daemon_mod.clear_active_session()
    try:
        library = tmp_path / "lib"
        library.mkdir(parents=True)
        factory, db_path = _file_db(tmp_path)
        settings = _minimal_settings(library)
        big = library / "big.mkv"
        big.write_bytes(b"a" * (3 * 1024 * 1024))
        item_id: int
        db = factory()
        try:
            db.add(Setting(key="hive_enabled", value="true", is_secret=False, updated_at=NOW))
            media = Media(type="film", title="Film demo", year=2001, tmdb_id=900002)
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, file_path=str(big), downloaded=True)
            db.add(item)
            db.flush()
            item_id = item.id
            assert item_id is not None
            db.add(
                LibraryFile(
                    path=str(big),
                    size=big.stat().st_size,
                    parsed_title="Film demo",
                    parsed_year=2001,
                    media_type="film",
                    tmdb_id=900002,
                    media_id=media.id,
                    media_item_id=item_id,
                    status="matched",
                    confidence="high",
                    seen_at=NOW,
                )
            )
            db.flush()
            grant_share_intent(db, item_id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
            db.commit()
        finally:
            db.close()

        stop = threading.Event()

        def api_writer():
            conn = sqlite3.connect(db_path, timeout=15)
            conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
            while not stop.is_set():
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO setting(key, value, is_secret, updated_at) VALUES (?,?,?,?)",
                        ("probe", "1", 0, NOW.isoformat()),
                    )
                    conn.commit()
                except sqlite3.OperationalError:
                    time.sleep(0.05)
                else:
                    time.sleep(0.02)
            conn.close()

        def hasher():
            session = factory()
            try:
                for _ in range(24):
                    hive_fingerprint_tick(session, settings)
                    try:
                        session.commit()
                    except OperationalError as exc:
                        if is_sqlite_locked(exc):
                            session.rollback()
                    time.sleep(0.05)
            finally:
                session.close()

        w = threading.Thread(target=api_writer)
        h = threading.Thread(target=hasher)
        w.start()
        h.start()
        h.join(timeout=20)
        stop.set()
        w.join(timeout=5)
        fp_db = factory()
        try:
            fp = fp_db.get(HiveFileFingerprint, item_id)
            assert fp is not None and fp.sha256_hex
        finally:
            fp_db.close()
    finally:
        daemon_mod.clear_active_session()
