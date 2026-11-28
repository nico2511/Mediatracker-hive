"""RUC2 : garde de synchronisation catalogue Hive (comptages d'appels, pas de seuils temps)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import delete
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login, write_sparse_video_file
from tests.test_hive_catalog_sharing_worker import NOW, _enable_hive, _join_ruche, _origin_id

import mediatracker.hive.catalog_sync_gate as catalog_sync_gate_module
from mediatracker.db.models import LibraryFile, Media, MediaItem, Setting
from mediatracker.hive.catalog import sync_hive_catalog
from mediatracker.hive.catalog_sync_gate import (
    HIVE_CATALOG_FULL_SYNC_INTERVAL_KEY,
    HIVE_CATALOG_WORKER_SYNC_STATE_KEY,
    mark_hive_catalog_sync_needed,
    record_hive_catalog_full_sync,
    should_run_full_hive_catalog_sync,
)
from mediatracker.hive.share_scope_registry import clear_share_scope, persist_share_scope
from mediatracker.library.scanner import _mark_absent
from mediatracker.settings_store import update_settings


def reset_worker_startup_full_catalog_sync_for_tests() -> None:
    catalog_sync_gate_module._worker_process_needs_initial_full_sync = True


def _reset_gate_state_for_tests(db) -> None:
    db.execute(
        delete(Setting).where(
            Setting.key.in_([HIVE_CATALOG_WORKER_SYNC_STATE_KEY, HIVE_CATALOG_FULL_SYNC_INTERVAL_KEY])
        )
    )
    reset_worker_startup_full_catalog_sync_for_tests()
    db.flush()


def _generation_pair(db) -> tuple[int, int]:
    row = db.get(Setting, HIVE_CATALOG_WORKER_SYNC_STATE_KEY)
    if row is None or not (row.value or "").strip():
        return 0, 0
    raw = json.loads(row.value)
    return int(raw.get("generation", 0)), int(raw.get("applied_generation", 0))


@pytest.fixture(autouse=True)
def _reset_gate_state(client, app):
    db = app.state.session_factory()
    try:
        _reset_gate_state_for_tests(db)
        db.commit()
    finally:
        db.close()
    yield
    db = app.state.session_factory()
    try:
        _reset_gate_state_for_tests(db)
        db.commit()
    finally:
        db.close()


def _seed_shareable_films(app, settings, *, count: int) -> bytes:
    from mediatracker.core.storage import storage_layout

    layout = storage_layout(app.state.session_factory(), settings)
    origin = _origin_id(settings)
    ruche_id = bytes([0xAA] * 15 + [0x01])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings=settings)
        from mediatracker.hive.share_policy import grant_share_intent

        for i in range(count):
            media = Media(type="film", title=f"Catalogue fictif {i}", year=2020, tmdb_id=90000 + i)
            db.add(media)
            db.flush()
            video = layout.movies_dir / f"fictif-{i}.mkv"
            video.parent.mkdir(parents=True, exist_ok=True)
            write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
            item = MediaItem(
                media_id=media.id,
                downloaded=True,
                file_path=str(video),
                release_quality="1080p",
                release_language="VFF",
            )
            db.add(item)
            db.flush()
            grant_share_intent(db, item.id)
        _reset_gate_state_for_tests(db)
        db.commit()
    finally:
        db.close()
    return ruche_id


def test_quiet_second_pass_skips_per_item_work(client, app, settings, monkeypatch):
    login(client)
    _enable_hive(client)
    _seed_shareable_films(app, settings, count=12)
    factory = app.state.session_factory

    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW)
        gen, _ = _generation_pair(db)
        record_hive_catalog_full_sync(db, now=NOW, applied_generation=gen)
        db.commit()

    hooks = {"sync": 0, "fp": 0, "idx": 0, "possede": 0}
    monkeypatch.setattr(
        "mediatracker.hive.catalog._sync_one_media_item_hive_catalog",
        lambda *a, **k: hooks.__setitem__("sync", hooks["sync"] + 1),
    )
    monkeypatch.setattr(
        "mediatracker.hive.catalog.ensure_fingerprint_row_for_item",
        lambda *a, **k: hooks.__setitem__("fp", hooks["fp"] + 1),
    )
    monkeypatch.setattr(
        "mediatracker.hive.catalog.FileStatusIndex.load",
        lambda *a, **k: hooks.__setitem__("idx", hooks["idx"] + 1) or None,
    )
    monkeypatch.setattr(
        "mediatracker.hive.signal.sync_possede_for_item",
        lambda *a, **k: hooks.__setitem__("possede", hooks["possede"] + 1),
    )

    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()

    assert hooks == {"sync": 0, "fp": 0, "idx": 0, "possede": 0}


def test_worker_startup_forces_full_scan_once(client, app, settings, monkeypatch):
    login(client)
    _enable_hive(client)
    _seed_shareable_films(app, settings, count=6)
    factory = app.state.session_factory
    sync_calls: list[int] = []

    monkeypatch.setattr(
        "mediatracker.hive.catalog._sync_one_media_item_hive_catalog",
        lambda *a, **k: sync_calls.append(1),
    )

    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW)
        record_hive_catalog_full_sync(db, now=NOW, applied_generation=_generation_pair(db)[0])
        db.commit()
    assert len(sync_calls) == 6
    sync_calls.clear()

    reset_worker_startup_full_catalog_sync_for_tests()
    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
    assert len(sync_calls) == 6
    sync_calls.clear()

    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
    assert len(sync_calls) == 0


def test_applied_generation_from_scan_start(client, app, settings, monkeypatch):
    login(client)
    _enable_hive(client)
    _seed_shareable_films(app, settings, count=3)
    factory = app.state.session_factory

    def _bump_on_sync(*_a, **_k):
        with factory() as inner:
            mark_hive_catalog_sync_needed(inner)
            inner.commit()

    monkeypatch.setattr("mediatracker.hive.catalog._sync_one_media_item_hive_catalog", _bump_on_sync)

    with factory() as db:
        gen_before, _ = _generation_pair(db)
        sync_hive_catalog(db, settings, now=NOW, force=True)
        db.commit()
        _, applied = _generation_pair(db)
        assert applied == gen_before


def test_mark_hive_public_address_via_settings(client, app, settings):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        gen0, _ = _generation_pair(db)
        update_settings(
            db,
            settings,
            {"hive_public_address": "https://mediatracker.example.com/nouvelle-adresse"},
        )
        db.commit()
        gen1, _ = _generation_pair(db)
        assert gen1 == gen0 + 1
    finally:
        db.close()


def test_mark_hive_catalog_auto_share_toggle(client, app, settings):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        gen0, _ = _generation_pair(db)
        update_settings(db, settings, {"hive_catalog_auto_share": True})
        db.commit()
        gen1, _ = _generation_pair(db)
        assert gen1 >= gen0 + 1
    finally:
        db.close()


def test_mark_hive_enabled_false_to_true(client, app, settings):
    login(client)
    db = app.state.session_factory()
    try:
        update_settings(db, settings, {"hive_enabled": True, "hive_instance_name": "banc-tests"})
        db.commit()
        gen1, _ = _generation_pair(db)
        assert gen1 >= 1
    finally:
        db.close()


def test_mark_scanner_mark_absent(client, app, settings, tmp_path):
    from mediatracker.core.storage import storage_layout

    login(client)
    _enable_hive(client)
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "absent-fictif.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Absent fictif", year=2022, tmdb_id=88001)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        lib_row = LibraryFile(
            path=str(video),
            size=PLAUSIBLE_VIDEO_BYTES,
            parsed_title="Absent fictif",
            media_type="film",
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
        db.add(lib_row)
        gen0, _ = _generation_pair(db)
        absent_path = str(video)
        if video.is_file():
            video.unlink()
        db.flush()
        marked = _mark_absent(db, set(), (layout.movies_dir,), NOW)
        if marked == 0:
            lib_row.status = "matched"
            lib_row.path = absent_path
            db.flush()
            marked = _mark_absent(db, set(), (layout.movies_dir,), NOW)
        assert marked >= 1
        gen1, _ = _generation_pair(db)
        assert gen1 >= gen0 + 1
        db.commit()
    finally:
        db.close()


def test_mark_create_ruche_and_member_list(client, app, settings):
    from tests.test_hive_t3 import _create_ruche_api

    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        gen0, _ = _generation_pair(db)
    finally:
        db.close()
    _create_ruche_api(client)
    db = app.state.session_factory()
    try:
        gen1, _ = _generation_pair(db)
        assert gen1 >= gen0 + 1
    finally:
        db.close()


def test_mark_share_scope_registry(client, app, settings):
    from sqlalchemy import select

    login(client)
    _enable_hive(client)
    _seed_shareable_films(app, settings, count=2)
    db = app.state.session_factory()
    try:
        media_id = db.scalar(select(Media.id).limit(1))
        assert media_id is not None
        gen0, _ = _generation_pair(db)
        persist_share_scope(db, int(media_id), season=None, ruche_ids=[bytes([0xAA] * 15 + [0x01])])
        gen1, _ = _generation_pair(db)
        assert gen1 >= gen0 + 1
        clear_share_scope(db, int(media_id), season=None)
        gen2, _ = _generation_pair(db)
        assert gen2 >= gen1 + 1
        db.commit()
    finally:
        db.close()


def test_mark_finalize_download_row(client, app, settings, monkeypatch):
    from mediatracker.core.pipeline.queue import _finalize_download_row

    login(client)
    _enable_hive(client)
    factory = app.state.session_factory
    with factory() as db:
        gen0, _ = _generation_pair(db)
        db.commit()

    from pathlib import Path

    with factory() as db:
        media = Media(type="film", title="Dl fictif", year=2024, tmdb_id=88002, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        from mediatracker.db.models import Download

        row = Download(media_item_id=item.id, state="post_processing", progress=50)
        db.add(row)
        db.commit()
        dest = Path(settings.data_dir) / "media" / "Films" / "Dl fictif" / "f.mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * 64)
        _finalize_download_row(
            db, row, item, media, dest, NOW, settings, store_release=False, library_path_for_import=str(dest)
        )
        gen1, _ = _generation_pair(db)
        assert gen1 >= gen0 + 1
        db.commit()


def test_gossip_pull_without_accepted_does_not_mark(client, app, settings, monkeypatch):
    login(client)
    _enable_hive(client)
    ruche_id = _seed_shareable_films(app, settings, count=1)
    monkeypatch.setattr("mediatracker.hive.peer_gossip.ingest_signed_entries", lambda *a, **k: 0)
    monkeypatch.setattr("mediatracker.hive.signal.ingest_signed_signals", lambda *a, **k: 0)
    monkeypatch.setattr(
        "mediatracker.hive.gossip_exchange.gossip_sync_once",
        lambda **_k: type(
            "Resp",
            (),
            {
                "status_code": 200,
                "json": lambda self: {
                    "entries": [{"payload": {}, "signature_b64": "x"}],
                    "signals": [],
                    "vector": {},
                    "signal_vector": {},
                },
            },
        )(),
    )
    from mediatracker.hive.gossip_exchange import pull_gossip_from_peer

    db = app.state.session_factory()
    try:
        gen0, _ = _generation_pair(db)
        pull_gossip_from_peer(
            db,
            settings,
            ruche_id=ruche_id,
            peer_base_url="https://mediatracker.example.com",
            now=NOW,
        )
        gen1, _ = _generation_pair(db)
        assert gen1 == gen0
        db.rollback()
    finally:
        db.close()


def test_interval_setting_via_api(client, app, settings, monkeypatch):
    login(client)
    _enable_hive(client)
    _seed_shareable_films(app, settings, count=4)
    sync_calls: list[int] = []

    monkeypatch.setattr(
        "mediatracker.hive.catalog._sync_one_media_item_hive_catalog",
        lambda *a, **k: sync_calls.append(1),
    )

    r = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_catalog_full_sync_interval_sec": 3600}},
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
    factory = app.state.session_factory
    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW)
        gen, _ = _generation_pair(db)
        record_hive_catalog_full_sync(db, now=NOW, applied_generation=gen)
        db.commit()
    sync_calls.clear()
    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW + timedelta(seconds=30))
        db.commit()
    assert len(sync_calls) == 0

    sync_calls.clear()
    with factory() as db:
        sync_hive_catalog(db, settings, now=NOW + timedelta(seconds=3601))
        db.commit()
    assert len(sync_calls) == 4


def test_interval_setting_bounds_via_api(client, app, settings):
    login(client)
    _enable_hive(client)
    r_low = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_catalog_full_sync_interval_sec": 60}},
        headers=csrf(client),
    )
    assert r_low.status_code == 200, r_low.text
    r_high = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_catalog_full_sync_interval_sec": 604800}},
        headers=csrf(client),
    )
    assert r_high.status_code == 200, r_high.text
    r_below = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_catalog_full_sync_interval_sec": 59}},
        headers=csrf(client),
    )
    assert r_below.status_code == 422, r_below.text
    r_above = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_catalog_full_sync_interval_sec": 604801}},
        headers=csrf(client),
    )
    assert r_above.status_code == 422, r_above.text


def test_clock_skew_triggers_full_sync(client, app, settings):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        record_hive_catalog_full_sync(db, now=NOW + timedelta(hours=1), applied_generation=0)
        db.flush()
        assert should_run_full_hive_catalog_sync(db, now=NOW) is True
        db.rollback()
    finally:
        db.close()