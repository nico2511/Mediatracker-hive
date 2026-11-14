"""Empreintes : la progression doit survivre à un rollback d'une étape worker ultérieure."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.test_hive_catalog_sharing_worker import _enable_hive

import mediatracker.worker as worker
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import HiveFileFingerprint, LibraryFile, Media, MediaItem
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item
from mediatracker.hive.share_policy import grant_share_intent

NOW = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_fingerprint_daemon_session():
    from mediatracker.hive import fingerprint_daemon as daemon_mod

    daemon_mod.clear_active_session()
    yield
    daemon_mod.clear_active_session()


def test_fingerprint_partial_survives_hive_catalog_failure_in_worker_pass(client, app, settings, monkeypatch):
    """Le processus empreintes commit hors worker : un rollback catalogue ne l'efface pas."""
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "worker-commit.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    content = b"x" * (2 * 1024 * 1024)
    video.write_bytes(content)

    db = app.state.session_factory()
    try:
        media = Media(type="film", title="WorkerCommit", year=2026, tmdb_id=901)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=len(content),
                parsed_title="WorkerCommit",
                parsed_year=2026,
                media_type="film",
                tmdb_id=901,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.flush()
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        item_id = item.id
        db.commit()
    finally:
        db.close()

    def _boom(*_a, **_k):
        raise RuntimeError("catalogue hive simulé")

    from mediatracker.hive.fingerprint import hive_fingerprint_tick

    tick_db = app.state.session_factory()
    try:
        hive_fingerprint_tick(tick_db, settings, now=NOW)
        tick_db.commit()
        partial_before = int(tick_db.get(HiveFileFingerprint, item_id).partial_size or 0)
        assert partial_before > 0
    finally:
        tick_db.close()

    monkeypatch.setattr(worker, "sync_hive_catalog", _boom)

    session = app.state.session_factory()
    try:
        worker._run_worker_pass(session, settings, app.state.session_factory)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

    check = app.state.session_factory()
    try:
        fp = check.get(HiveFileFingerprint, item_id)
        assert fp is not None
        assert int(fp.partial_size or 0) == partial_before
    finally:
        check.close()


def test_fingerprint_tick_advances_when_db_path_not_canonical(client, app, settings):
    """Chemin DB non résolu : l'UPDATE conditionnel ne doit pas rester bloqué à vie."""
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "nested" / "canonical.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    content = b"z" * (512 * 1024)
    video.write_bytes(content)
    non_canonical = layout.movies_dir / "nested" / ".." / "nested" / "canonical.mkv"
    assert non_canonical.is_file()

    db = app.state.session_factory()
    try:
        media = Media(type="film", title="CanonicalPath", year=2026, tmdb_id=902)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(non_canonical))
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=len(content),
                parsed_title="CanonicalPath",
                parsed_year=2026,
                media_type="film",
                tmdb_id=902,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        db.flush()
        grant_share_intent(db, item.id)
        from mediatracker.hive.fingerprint_queue import enqueue_fingerprint_work

        enqueue_fingerprint_work(db, item.id, now=NOW)
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(non_canonical),
                size=len(content),
                mtime_ns=_mtime_ns(video),
                partial_size=0,
                updated_at=NOW,
            )
        )
        item_id = item.id
        db.commit()
    finally:
        db.close()

    from mediatracker.hive.fingerprint import hive_fingerprint_tick

    tick_db = app.state.session_factory()
    try:
        hive_fingerprint_tick(tick_db, settings)
        tick_db.commit()
    finally:
        tick_db.close()

    check = app.state.session_factory()
    try:
        fp = check.get(HiveFileFingerprint, item_id)
        assert fp is not None
        advanced = int(fp.partial_size or 0) > 0 or bool((fp.sha256_hex or "").strip())
        assert advanced, "aucune progression malgré un fichier lisible (chemin non canonique ?)"
    finally:
        check.close()


def _mtime_ns(path: Path) -> int:
    st = path.stat()
    mtime_ns = getattr(st, "st_mtime_ns", None)
    if mtime_ns is None:
        mtime_ns = int(st.st_mtime * 1_000_000_000)
    return int(mtime_ns)
