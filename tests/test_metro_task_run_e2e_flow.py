"""Chaîne fictive : _begin → _import_one → _finalize (redémarrage simulé)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from tests.conftest import login
from tests.test_video_integrity import _minimal_mkv

from mediatracker.config import Settings
from mediatracker.core.pipeline.queue import _begin, _finalize_download_row, _import_one
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider, TaskRun
from mediatracker.metro.collection import set_collection_duration
from mediatracker.metro.record import (
    flush_pass_buffer,
    load_pass_collection_flag,
    reset_metro_instrumentation_state,
)


@pytest.fixture(autouse=True)
def _reset_metro_e2e():
    reset_metro_instrumentation_state()
    yield
    reset_metro_instrumentation_state()


def _patch_begin_success(monkeypatch, link: Link) -> None:
    found = (link, "https://mediatracker.example.com/direct", "piece.mkv", len(_minimal_mkv()), "https://example.com/h")

    monkeypatch.setattr("mediatracker.core.pipeline.queue.magnet_cache_first", lambda *a, **k: None)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_manual_links", lambda *a, **k: None)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_manual_source_page", lambda *a, **k: None)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._from_named_source", lambda *a, **k: found)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.queue._usable_download_sources",
        lambda *a, **k: ["wawacity"],
    )
    monkeypatch.setattr(
        "mediatracker.core.precheck.should_bypass_remote_dedup",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "mediatracker.core.pipeline.queue.launch_free_required",
        lambda *a, **k: 1,
    )


def test_film_flow_begin_import_finalize_after_restart(client, app, monkeypatch, tmp_path):
    login(client)
    factory = app.state.session_factory
    settings: Settings = app.state.settings
    with factory() as db:
        set_collection_duration(db, "24h")
        db.commit()
    load_pass_collection_flag(factory)

    now = datetime.now(timezone.utc)
    payload = _minimal_mkv()
    with factory() as db:
        media = Media(type="film", title="Flux fictif e2e", followed=True, created_at=now)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        provider = Provider(
            name="bench-provider",
            kind="source",
            type="wawacity",
            enabled=True,
        )
        db.add(provider)
        db.flush()
        link = Link(
            media_item_id=item.id,
            provider_id=provider.id,
            kind="ddl",
            url="https://example.com/link",
            filename="piece.mkv",
            hoster="example",
            size=len(payload),
            external_id="bench-link-1",
        )
        db.add(link)
        db.flush()
        row = Download(
            media_item_id=item.id,
            link_id=link.id,
            state="pending",
            forced=True,
            created_at=now,
        )
        db.add(row)
        db.commit()
        download_id = row.id
        media_id = media.id
        item_id = item.id
        link_id = link.id

    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda db: True)
    monkeypatch.setattr(
        "mediatracker.hive.catalog.sync_hive_catalog_item",
        lambda *a, **k: None,
    )
    data = tmp_path / "data"
    (data / "downloads").mkdir(parents=True, exist_ok=True)
    (data / "media" / "Films").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", str(data))
    monkeypatch.setattr(settings, "config_dir", str(tmp_path))

    _patch_begin_success(monkeypatch, link)

    debrid = MagicMock()
    downloader = MagicMock()
    downloader.add.return_value = "gid-1"
    preference: list[str] = []

    with factory() as db:
        row = db.get(Download, download_id)
        item = db.get(MediaItem, item_id)
        media = db.get(Media, media_id)
        link = db.get(Link, link_id)
        assert row is not None and item is not None and media is not None
        outcome = _begin(
            db,
            row,
            now,
            preference,
            None,
            debrid,
            None,
            downloader,
            settings,
            None,
            [],
        )
        assert outcome == "started"
        db.commit()

    flushed = 0
    for _ in range(8):
        flushed = flush_pass_buffer(factory)
        if flushed:
            break
        time.sleep(0.05)
    assert flushed > 0
    with factory() as db:
        row = db.get(Download, download_id)
        assert row is not None
        assert row.metro_sequence_id
        expected_seq = row.metro_sequence_id

    reset_metro_instrumentation_state()
    load_pass_collection_flag(factory)

    source = data / "downloads" / "incomplete" / (row.filename or "piece.mkv")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)

    def _defer_finalize(db, row, item, media, dest, source_path, moment, setts, *, store_release=True):
        item.file_path = str(dest)
        item.downloaded = True
        item.downloaded_at = moment
        item.state = "downloaded"
        row.state = "post_processing"
        row.error = str(source_path)
        row.filename = source_path.name

    monkeypatch.setattr("mediatracker.core.pipeline.queue._finish_download", _defer_finalize)

    with factory() as db:
        row = db.get(Download, download_id)
        item = db.get(MediaItem, item_id)
        media = db.get(Media, media_id)
        _import_one(db, row, now, settings)
        db.commit()

    flushed_import = 0
    for _ in range(8):
        flushed_import = flush_pass_buffer(factory)
        if flushed_import:
            break
        time.sleep(0.05)
    assert flushed_import > 0

    reset_metro_instrumentation_state()
    load_pass_collection_flag(factory)

    library_dest = data / "media" / "Films" / "Flux fictif e2e" / "piece.mkv"
    library_dest.parent.mkdir(parents=True, exist_ok=True)
    if not library_dest.is_file():
        library_dest.write_bytes(payload)

    with factory() as db:
        row = db.get(Download, download_id)
        item = db.get(MediaItem, item_id)
        media = db.get(Media, media_id)
        _finalize_download_row(
            db,
            row,
            item,
            media,
            library_dest,
            now,
            settings,
            store_release=False,
            library_path_for_import=str(library_dest),
        )
        db.commit()

    for _ in range(8):
        if flush_pass_buffer(factory):
            break
        time.sleep(0.05)
    with factory() as db:
        rows = db.scalars(select(TaskRun).where(TaskRun.sequence_id == expected_seq).order_by(TaskRun.id)).all()
        assert rows
        assert {r.sequence_id for r in rows} == {expected_seq}
        assert all(r.media_id == media_id for r in rows), [r.station for r in rows if r.media_id is None]
        rng5 = next(r for r in rows if r.station == "RNG5")
        sui3 = next(r for r in rows if r.station == "SUI3")
        ruc2 = next(r for r in rows if r.station == "RUC2")
        assert sui3.parent_run_id == rng5.id
        assert ruc2.parent_run_id == rng5.id
        for station in ("DL1", "ADB3", "RNG1"):
            hit = next(r for r in rows if r.station == station)
            assert hit.media_id == media_id
            assert hit.sequence_id == expected_seq