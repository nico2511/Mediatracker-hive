"""Anti-doublon : chaque point d'enfilage utilise ``active_download_for_item``."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import func, select
from tests.conftest import catalog_min_logical_video_bytes, csrf, login
from tests.test_hive_fetch_grant_lot2 import (
    _ensure_origin_peer,
    _pick_match,
    _seed_catalogue_row,
    _setup_pair,
)
from tests.test_hive_t5 import NOW, URL_A, _seed_file_on_a

from mediatracker.core.force_download import force_downloads
from mediatracker.core.forget import forget_local_file
from mediatracker.core.redownload import redownload_item
from mediatracker.core.source_poll import _blocking_download
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.hive.source_poll import enqueue_hive_match

pytest_plugins = ["tests.test_hive_t5"]


def _film_item(client) -> tuple[int, int]:
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Boîte Démo", "year": 2026},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    mid = media.json()["id"]
    item = client.post(
        f"/api/v1/media/{mid}/items",
        json={"title": "Version"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    return mid, item.json()["id"]


def test_guard_create_download_api_no_duplicate(client, app):
    _, item_id = _film_item(client)
    body = {"media_item_id": item_id}
    first = client.post("/api/v1/downloads", json=body, headers=csrf(client))
    second = client.post("/api/v1/downloads", json=body, headers=csrf(client))
    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    db = app.state.session_factory()
    try:
        n = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item_id))
        assert n == 1
    finally:
        db.close()


def test_guard_links_start_reuses_active_download(client, app):
    mid, item_id = _film_item(client)
    payload = {
        "media_id": mid,
        "url": "https://rapidgator.net/file/alpha",
        "start": True,
    }
    first = client.post("/api/v1/links", json=payload, headers=csrf(client))
    second = client.post(
        "/api/v1/links",
        json={"media_id": mid, "url": "https://turbobit.net/file/beta", "start": True},
        headers=csrf(client),
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["download_id"] == second.json()["download_id"]
    db = app.state.session_factory()
    try:
        n = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item_id))
        assert n == 1
    finally:
        db.close()


def test_guard_enqueue_hive_reuses_same_download(transfer_pair):
    app_a, app_b = transfer_pair
    from starlette.testclient import TestClient

    payload = os.urandom(catalog_min_logical_video_bytes())
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        origin, fid, sha = _seed_file_on_a(app_a, rid, payload)
        db = app_b.state.session_factory()
        try:
            cle = {"type": "film", "tmdb_id": 9010, "version": "HD+VFF"}
            media = Media(
                type="film",
                title="Demo",
                year=2020,
                tmdb_id=9010,
                followed=True,
                pref_quality="HD",
                pref_language="VFF",
            )
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, state="missing")
            db.add(item)
            db.flush()
            _seed_catalogue_row(
                db,
                cle=cle,
                version="HD+VFF",
                sha=sha,
                joignable=True,
                ruche_hex=rid.hex(),
                taille=len(payload),
            )
            from mediatracker.db.models import HiveCatalogue

            cat = db.scalar(select(HiveCatalogue).limit(1))
            sources = __import__("json").loads(cat.sources_json)
            sources[0]["origine"] = origin
            sources[0]["file_id_hex"] = fid
            cat.sources_json = __import__("json").dumps(sources, separators=(",", ":"))
            _ensure_origin_peer(db, rid, origin, URL_A)
            db.commit()
            match = _pick_match(db, media, item)
            first = enqueue_hive_match(db, app_b.state.settings, media, item, match, now=NOW)
            assert first is not None
            db.flush()
            item = db.get(MediaItem, item.id)
            match2 = _pick_match(db, media, item)
            assert match2 is not None
            second = enqueue_hive_match(db, app_b.state.settings, media, item, match2, now=NOW)
            db.commit()
            assert first is not None and second is not None
            assert first.id == second.id
            n = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item.id))
            assert n == 1
        finally:
            db.close()


def test_guard_source_poll_blocking_skips_second_pending(client, app):
    db = app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        media = Media(type="film", title="Demo", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="available")
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="queued", created_at=now))
        db.commit()
        assert _blocking_download(db, item.id) is True
        before = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item.id))
        if not _blocking_download(db, item.id):
            db.add(Download(media_item_id=item.id, state="pending", created_at=now))
            db.flush()
        after = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item.id))
        assert before == after == 1
    finally:
        db.close()


def test_guard_force_download_busy_no_second_row(client, app, settings):
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Brume Lac", "year": 2026},
        headers=csrf(client),
    )
    assert media.status_code == 201
    mid = media.json()["id"]
    ep = client.post(
        f"/api/v1/media/{mid}/items",
        json={"season": 1, "episode": 2, "title": "Deux"},
        headers=csrf(client),
    )
    assert ep.status_code == 201
    item_id = ep.json()["id"]
    db = app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        media_row = db.get(Media, mid)
        item = db.get(MediaItem, item_id)
        db.add(Download(media_item_id=item_id, state="downloading", created_at=now))
        db.commit()
        result = force_downloads(db, settings, media_row, [item], confirm=False, now=now)
        assert result.busy and not result.queued
        n = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item_id))
        assert n == 1
    finally:
        db.close()


def test_guard_redownload_busy_no_second_row(client, app, settings):
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Brume Lac", "year": 2026},
        headers=csrf(client),
    )
    assert media.status_code == 201
    mid = media.json()["id"]
    ep = client.post(
        f"/api/v1/media/{mid}/items",
        json={"season": 1, "episode": 3, "title": "Trois"},
        headers=csrf(client),
    )
    assert ep.status_code == 201
    item_id = ep.json()["id"]
    db = app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        media_row = db.get(Media, mid)
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = "/tmp/ghost/demo.S01E03.mkv"
        forget_local_file(db, media_row, item, now)
        db.add(Download(media_item_id=item_id, state="pending", created_at=now))
        db.commit()
        out = redownload_item(db, settings, media_row, item, now=now)
        assert out.status == "busy"
        n = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item_id))
        assert n == 1
    finally:
        db.close()
