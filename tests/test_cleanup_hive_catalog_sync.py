"""Corbeille / restauration / rattachement : sync catalogue Hive immédiat."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login
from tests.test_hive_catalog_stale_offer import NOW, _seed_video_item
from tests.test_hive_t6 import _enable, _ruche

from mediatracker.core.cleanup import delete_files
from mediatracker.db.models import HiveEntry, HiveFetchGrant, HiveFileFingerprint, MediaItem
from mediatracker.hive.catalog import sync_hive_catalog_item
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.library.scanner import attach_known_files

pytest_plugins = ["tests.test_hive_t5"]


def _library_video(settings, name: str) -> Path:
    movies = Path(settings.data_dir) / "media" / "Films"
    path = movies / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * PLAUSIBLE_VIDEO_BYTES)
    return path


def _hive_on(client):
    _enable(client)
    _ruche(client)


def test_trash_publishes_tombstone_without_worker(client, app, settings, monkeypatch):
    _hive_on(client)
    path = _library_video(settings, "HiveTrash/Fictif.mkv")
    db = app.state.session_factory()
    try:
        media, item, _rid, _origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88100,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="a" * 64,
            path=path,
        )
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        media_id, item_id = media.id, item.id
        last = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item.id).order_by(HiveEntry.seq.desc()))
        assert last is not None and last.etat == "disponible"
    finally:
        db.close()

    worker_called: list[int] = []
    monkeypatch.setattr(
        "mediatracker.hive.catalog.sync_hive_catalog",
        lambda *_a, **_k: worker_called.append(1),
    )

    db = app.state.session_factory()
    try:
        delete_files(db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW)
        last = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item_id).order_by(HiveEntry.seq.desc()))
        assert last is not None and last.etat == "retire"
        assert worker_called == []
    finally:
        db.close()


def test_restore_relists_after_trash(client, app, settings):
    _hive_on(client)
    path = _library_video(settings, "HiveRestore/Fictif.mkv")
    db = app.state.session_factory()
    try:
        media, item, _rid, _origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88101,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="b" * 64,
            path=path,
        )
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        media_id, item_id = media.id, item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        outcome = delete_files(
            db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW
        )
        entry_id = outcome.entries[0].id
        db.commit()
    finally:
        db.close()

    login(client)
    restored = client.post(f"/api/v1/trash/{entry_id}/restore", headers=csrf(client))
    assert restored.status_code == 200, restored.text

    db = app.state.session_factory()
    try:
        item_row = db.get(MediaItem, item_id)
        assert item_row is not None and item_row.file_path == str(path.resolve())
        fp = db.get(HiveFileFingerprint, item_id)
        assert fp is not None and fp.path == str(path.resolve())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        fp.sha256_hex = digest
        fp.partial_sha256_hex = digest
        fp.partial_size = int(fp.size or 0)
        sync_hive_catalog_item(db, settings, item_id, now=NOW)
        db.commit()
        last = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item_id).order_by(HiveEntry.seq.desc()))
        assert last is not None and last.etat == "disponible"
    finally:
        db.close()


def test_trash_revokes_active_fetch_grant(client, app, settings):
    _hive_on(client)
    path = _library_video(settings, "HiveGrant/Fictif.mkv")
    db = app.state.session_factory()
    try:
        media, item, ruche_id, origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88102,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="c" * 64,
            path=path,
        )
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        file_id = bytes.fromhex("cc" * 16)
        db.add(
            HiveFetchGrant(
                grant_sha256_hex="d" * 64,
                ruche_id=ruche_id,
                file_id=file_id,
                requester_instance_id="requester-test-instance",
                media_item_id=item.id,
                content_size=path.stat().st_size,
                content_mtime_ns=getattr(path.stat(), "st_mtime_ns", 0) or int(path.stat().st_mtime * 1e9),
                content_sha256_hex="c" * 64,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        db.commit()
        media_id, item_id = media.id, item.id
        grant_id = db.scalar(select(HiveFetchGrant.id).limit(1))
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        delete_files(db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW)
        row = db.get(HiveFetchGrant, grant_id)
        assert row is not None and row.revoked_at is not None
    finally:
        db.close()


def test_hive_sync_failure_does_not_block_trash(client, app, settings, monkeypatch):
    _hive_on(client)
    path = _library_video(settings, "HiveFail/Fictif.mkv")
    db = app.state.session_factory()
    try:
        media, item, _rid, _origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88103,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="e" * 64,
            path=path,
        )
        grant_share_intent(db, item.id)
        db.commit()
        media_id, item_id = media.id, item.id
    finally:
        db.close()

    def _boom(*_a, **_k):
        raise RuntimeError("hive sync boom")

    monkeypatch.setattr("mediatracker.hive.catalog.sync_hive_catalog_item", _boom)

    db = app.state.session_factory()
    try:
        outcome = delete_files(
            db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW
        )
        assert outcome.entries
        item_row = db.get(MediaItem, item_id)
        assert item_row is not None and not item_row.downloaded
    finally:
        db.close()


def test_attach_known_files_triggers_catalog_sync(client, app, settings, monkeypatch):
    _hive_on(client)
    calls: list[int] = []
    monkeypatch.setattr(
        "mediatracker.hive.catalog.sync_hive_catalog_item",
        lambda _db, _settings, item_id, **_: calls.append(int(item_id)),
    )
    data = Path(settings.data_dir)
    movies = data / "media" / "Films"
    folder = movies / "Rattache [tmdbid-88104]"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "Fictif (2099).mkv"
    video.write_bytes(b"v" * PLAUSIBLE_VIDEO_BYTES)

    db = app.state.session_factory()
    try:
        from mediatracker.db.models import Media

        media = Media(type="film", title="Fictif", year=2099, tmdb_id=88104)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing", downloaded=False)
        db.add(item)
        db.commit()
        grant_share_intent(db, item.id)
        db.commit()
        attached = attach_known_files(db, settings, media)
        db.commit()
        assert attached == 1
        assert calls == [int(item.id)]
    finally:
        db.close()



def test_trash_does_nothing_hive_when_disabled(client, app, settings, monkeypatch):
    path = _library_video(settings, "HiveOff/Fictif.mkv")
    calls: list[int] = []
    monkeypatch.setattr(
        "mediatracker.hive.catalog.sync_hive_catalog_item",
        lambda _db, _settings, item_id, **_: calls.append(int(item_id)),
    )
    db = app.state.session_factory()
    try:
        from mediatracker.db.models import Media

        media = Media(type="film", title="Fictif", year=2099, tmdb_id=88105)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(path),
            state="downloaded",
        )
        db.add(item)
        db.commit()
        media_id, item_id = media.id, item.id
        outcome = delete_files(
            db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW
        )
        assert outcome.entries
        item_row = db.get(MediaItem, item_id)
        assert item_row is not None and not item_row.downloaded
        assert calls == []
    finally:
        db.close()


def test_trash_revokes_only_the_trashed_episode_grant(client, app, settings):
    _hive_on(client)
    ep1 = _library_video(settings, "HiveSeries/S01E01.mkv")
    ep2 = _library_video(settings, "HiveSeries/S01E02.mkv")
    db = app.state.session_factory()
    try:
        media, item1, ruche_id, _origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88106,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="f" * 64,
            path=ep1,
        )
        media.type = "series"
        media.title = "Série démo"
        item1.season = 1
        item1.episode = 1
        item2 = MediaItem(
            media_id=media.id,
            season=1,
            episode=2,
            downloaded=True,
            file_path=str(ep2),
            state="downloaded",
            release_quality="HD",
            release_language="VFF",
        )
        db.add(item2)
        db.flush()
        grant_share_intent(db, item2.id)
        db.add(
            HiveFetchGrant(
                grant_sha256_hex="11" * 32,
                ruche_id=ruche_id,
                file_id=bytes.fromhex("11" * 16),
                requester_instance_id="requester-test-instance",
                media_item_id=item1.id,
                content_size=ep1.stat().st_size,
                content_mtime_ns=getattr(ep1.stat(), "st_mtime_ns", 0) or int(ep1.stat().st_mtime * 1e9),
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        db.add(
            HiveFetchGrant(
                grant_sha256_hex="22" * 32,
                ruche_id=ruche_id,
                file_id=bytes.fromhex("22" * 16),
                requester_instance_id="requester-test-instance",
                media_item_id=item2.id,
                content_size=ep2.stat().st_size,
                content_mtime_ns=getattr(ep2.stat(), "st_mtime_ns", 0) or int(ep2.stat().st_mtime * 1e9),
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        db.commit()
        media_id, item1_id, item2_id = media.id, item1.id, item2.id
        g1 = db.scalar(select(HiveFetchGrant).where(HiveFetchGrant.media_item_id == item1_id))
        g2 = db.scalar(select(HiveFetchGrant).where(HiveFetchGrant.media_item_id == item2_id))
        assert g1 is not None and g2 is not None
        g1_id, g2_id = g1.id, g2.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        delete_files(db, settings, media_id=media_id, scope="episode", season=None, item_id=item1_id, now=NOW)
        row1 = db.get(HiveFetchGrant, g1_id)
        row2 = db.get(HiveFetchGrant, g2_id)
        assert row1 is not None and row1.revoked_at is not None
        assert row2 is not None and row2.revoked_at is None
        other = db.get(MediaItem, item2_id)
        assert other is not None and other.downloaded
    finally:
        db.close()


def test_restore_does_not_relist_before_fingerprint(client, app, settings):
    _hive_on(client)
    path = _library_video(settings, "HiveRestorePending/Fictif.mkv")
    db = app.state.session_factory()
    try:
        media, item, _rid, _origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88107,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="aa" * 32,
            path=path,
        )
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        media_id, item_id = media.id, item.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        outcome = delete_files(
            db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW
        )
        entry_id = outcome.entries[0].id
        db.commit()
    finally:
        db.close()

    login(client)
    restored = client.post(f"/api/v1/trash/{entry_id}/restore", headers=csrf(client))
    assert restored.status_code == 200, restored.text

    db = app.state.session_factory()
    try:
        fp = db.get(HiveFileFingerprint, item_id)
        assert fp is not None
        assert not (fp.sha256_hex or "").strip()
        last = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item_id).order_by(HiveEntry.seq.desc()))
        assert last is not None and last.etat != "disponible"
    finally:
        db.close()


def test_attach_known_files_skips_sync_when_path_unchanged(client, app, settings, monkeypatch):
    _hive_on(client)
    calls: list[int] = []
    monkeypatch.setattr(
        "mediatracker.hive.catalog.sync_hive_catalog_item",
        lambda _db, _settings, item_id, **_: calls.append(int(item_id)),
    )
    data = Path(settings.data_dir)
    folder = data / "media" / "Films" / "Rattache2 [tmdbid-88108]"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "Fictif (2099).mkv"
    video.write_bytes(b"v" * PLAUSIBLE_VIDEO_BYTES)

    db = app.state.session_factory()
    try:
        from mediatracker.db.models import Media

        media = Media(type="film", title="Fictif", year=2099, tmdb_id=88108)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing", downloaded=False)
        db.add(item)
        db.commit()
        grant_share_intent(db, item.id)
        db.commit()
        assert attach_known_files(db, settings, media) == 1
        db.commit()
        assert calls == [int(item.id)]
        calls.clear()
        assert attach_known_files(db, settings, media) == 1
        db.commit()
        assert calls == []
    finally:
        db.close()


def test_grant_revoked_even_if_catalog_sync_fails(client, app, settings, monkeypatch):
    _hive_on(client)
    path = _library_video(settings, "HiveGrantFail/Fictif.mkv")
    db = app.state.session_factory()
    try:
        media, item, ruche_id, _origin = _seed_video_item(
            db,
            settings,
            tmdb_id=88109,
            size=PLAUSIBLE_VIDEO_BYTES,
            sha="bb" * 32,
            path=path,
        )
        db.add(
            HiveFetchGrant(
                grant_sha256_hex="ee" * 32,
                ruche_id=ruche_id,
                file_id=bytes.fromhex("ee" * 16),
                requester_instance_id="requester-test-instance",
                media_item_id=item.id,
                content_size=path.stat().st_size,
                content_mtime_ns=getattr(path.stat(), "st_mtime_ns", 0) or int(path.stat().st_mtime * 1e9),
                content_sha256_hex="bb" * 32,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        db.commit()
        media_id, item_id = media.id, item.id
        grant_id = db.scalar(select(HiveFetchGrant.id).limit(1))
    finally:
        db.close()

    monkeypatch.setattr(
        "mediatracker.hive.catalog.sync_hive_catalog_item",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("hive sync boom")),
    )
    db = app.state.session_factory()
    try:
        outcome = delete_files(
            db, settings, media_id=media_id, scope="episode", season=None, item_id=item_id, now=NOW
        )
        assert outcome.entries
        row = db.get(HiveFetchGrant, grant_id)
        assert row is not None and row.revoked_at is not None
    finally:
        db.close()
