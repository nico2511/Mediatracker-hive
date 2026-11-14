"""Hive T7 : API interface (catalogue fusionné sans provenance)."""

from __future__ import annotations

import json

from tests.conftest import catalog_min_logical_video_bytes, login
from tests.test_hive_t6 import NOW, _enable, _ingest, _ruche

pytest_plugins = ["tests.test_hive_t1"]


def test_overview_disabled(client):
    login(client)
    body = client.get("/api/v1/hive/overview").json()
    assert body["enabled"] is False


def test_catalogue_sanitized(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    try:
        _ingest(
            db,
            settings,
            ruche_id=rid,
            tmdb_id=8802,
            season=1,
            episode=2,
            version="HD+VFF",
            sha="b" * 64,
            joignable=True,
            seq=1,
            size=catalog_min_logical_video_bytes(),
        )
        from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store

        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue").json()
    assert page["items"]
    work = page["items"][0]
    assert work["title"] == "Oeuvre fictive"
    assert "origine" not in json.dumps(work).lower()
    assert work["versions"]
    assert "origine" not in json.dumps(page).lower()
    assert "ruche_id_hex" not in json.dumps(page)


def test_catalogue_off_404(client):
    login(client)
    assert client.get("/api/v1/hive/catalogue").status_code == 404


def test_batch_sharing_and_exclusion(client):
    from tests.conftest import csrf
    from tests.test_hive_t2 import _enable_hive

    from mediatracker.db.models import Media, MediaItem

    login(client)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        media = Media(type="film", title="Fiction A", year=2099, tmdb_id=9901)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        item_id = item.id
        db.commit()
    finally:
        db.close()
    batch = client.get(f"/api/v1/hive/items/sharing?ids={item_id}").json()
    assert len(batch) == 1
    assert batch[0]["media_item_id"] == item_id
    assert "excluded_ruche_ids" in batch[0]
    r = client.patch(
        f"/api/v1/hive/items/{item_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert r.status_code == 204
    one = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert one["exclude_all"] is True


def test_media_sharing_summary(client):
    from tests.test_hive_t2 import _enable_hive

    from mediatracker.db.models import Media, MediaItem

    login(client)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        media = Media(type="film", title="Fiction B", year=2098, tmdb_id=9902)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.commit()
        media_id = media.id
    finally:
        db.close()
    summary = client.get(f"/api/v1/hive/media/{media_id}/sharing-summary").json()
    assert summary["media_id"] == media_id
    assert summary["shared"] is False


def test_ruche_detail_quota(client):
    from tests.conftest import csrf
    from tests.test_hive_t2 import _enable_hive
    from tests.test_hive_t6 import _ruche

    login(client)
    _enable_hive(client)
    rid = _ruche(client)
    detail = client.get(f"/api/v1/hive/ruches/{rid.hex()}/detail").json()
    assert detail["ruche_id_hex"] == rid.hex()
    assert detail["weekly_quota_bytes"] > 0
    assert isinstance(detail["members"], list)
    patch = client.patch(
        f"/api/v1/hive/ruches/{rid.hex()}/quota",
        json={"weekly_quota_bytes": 50_000_000_000},
        headers=csrf(client),
    )
    assert patch.status_code == 204


def test_patch_hive_core_settings_multi_field(client):
    from tests.conftest import csrf
    from tests.test_hive_t2 import _enable_hive

    login(client)
    _enable_hive(client)
    r = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_public_address": "https://mtr.exemple.fr",
                "hive_transfer_parallel": 2,
                "hive_transfer_send_bps": 0,
                "hive_transfer_recv_bps": 0,
                "hive_transfer_window_start": 0,
                "hive_transfer_window_end": 1440,
                "download_source_order": "wawacity,hive,comet",
            }
        },
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text
