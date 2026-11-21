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
    assert body["next_gossip_at"] is None
    assert body["last_gossip_at"] is None


def test_overview_gossip_schedule_iso(client):
    login(client)
    _enable(client)
    from datetime import datetime

    from mediatracker.db.models import Setting
    from mediatracker.hive.gossip_worker import HIVE_GOSSIP_LAST_AT_KEY, HIVE_GOSSIP_NEXT_AT_KEY

    db = client.app.state.session_factory()
    try:
        db.add(Setting(key=HIVE_GOSSIP_NEXT_AT_KEY, value="2025-06-01T12:10:00Z"))
        db.add(Setting(key=HIVE_GOSSIP_LAST_AT_KEY, value="2025-06-01T12:00:00Z"))
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/hive/overview").json()
    assert body["enabled"] is True
    assert body["next_gossip_at"] == "2025-06-01T12:10:00Z"
    assert body["last_gossip_at"] == "2025-06-01T12:00:00Z"
    assert datetime.fromisoformat(body["next_gossip_at"].replace("Z", "+00:00")).tzinfo is not None


def test_overview_disabled_hides_schedule_even_if_keys_exist(client):
    login(client)
    from mediatracker.db.models import Setting
    from mediatracker.hive.gossip_worker import HIVE_GOSSIP_LAST_AT_KEY, HIVE_GOSSIP_NEXT_AT_KEY

    db = client.app.state.session_factory()
    try:
        db.add(Setting(key=HIVE_GOSSIP_NEXT_AT_KEY, value="2025-06-01T12:10:00Z"))
        db.add(Setting(key=HIVE_GOSSIP_LAST_AT_KEY, value="2025-06-01T12:00:00Z"))
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/hive/overview").json()
    assert body["enabled"] is False
    assert body["next_gossip_at"] is None
    assert body["last_gossip_at"] is None


def test_overview_normalizes_offset_iso_to_z(client):
    login(client)
    _enable(client)
    from mediatracker.db.models import Setting
    from mediatracker.hive.gossip_worker import HIVE_GOSSIP_LAST_AT_KEY, HIVE_GOSSIP_NEXT_AT_KEY

    db = client.app.state.session_factory()
    try:
        db.add(Setting(key=HIVE_GOSSIP_NEXT_AT_KEY, value="2025-06-01T12:10:00+00:00"))
        db.add(Setting(key=HIVE_GOSSIP_LAST_AT_KEY, value="2025-06-01T12:00:00+00:00"))
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/hive/overview").json()
    assert body["next_gossip_at"] == "2025-06-01T12:10:00Z"
    assert body["last_gossip_at"] == "2025-06-01T12:00:00Z"


def test_gossip_tick_writes_last_at_then_commits(client):
    login(client)
    _enable(client)
    from datetime import datetime, timezone

    from mediatracker.hive.gossip_worker import gossip_schedule_iso, hive_gossip_tick

    db = client.app.state.session_factory()
    try:
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        started = hive_gossip_tick(db, client.app.state.settings, now=now, force=True)
        assert started is True
        db.commit()
        next_iso, last_iso = gossip_schedule_iso(db)
    finally:
        db.close()

    assert last_iso == "2026-01-15T12:00:00Z"
    assert next_iso is not None
    assert next_iso > last_iso


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


def test_batch_media_sharing_summary(client):
    from tests.test_hive_t2 import _enable_hive

    from mediatracker.db.models import Media, MediaItem

    login(client)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        first = Media(type="film", title="Fiction C", year=2099, tmdb_id=9903)
        second = Media(type="series", title="Serial D", year=2100, tmdb_id=9904)
        db.add(first)
        db.add(second)
        db.flush()
        db.add(MediaItem(media_id=first.id, state="missing"))
        db.add(MediaItem(media_id=second.id, state="missing"))
        db.commit()
        id_a, id_b = first.id, second.id
    finally:
        db.close()
    batch = client.get(f"/api/v1/hive/media/sharing-summary?ids={id_a},{id_b}").json()
    assert len(batch) == 2
    assert {row["media_id"] for row in batch} == {id_a, id_b}
    for row in batch:
        assert row["shared"] is False


def test_batch_media_sharing_summary_query_budget(client, app):
    from datetime import date, datetime, timezone

    from sqlalchemy import event
    from tests.test_hive_t2 import _enable_hive

    from mediatracker.db.models import Media, MediaItem

    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    media_ids: list[int] = []
    try:
        for index in range(40):
            media = Media(
                type="series" if index % 2 == 0 else "film",
                title=f"Hive partage {index:03d}",
                year=2020 + (index % 5),
                tmdb_id=300_000 + index,
                followed=True,
            )
            db.add(media)
            db.flush()
            if media.type == "film":
                db.add(
                    MediaItem(
                        media_id=media.id,
                        season=None,
                        episode=None,
                        title="Film",
                        downloaded=True,
                        state="downloaded",
                        file_path=f"/fiction/hive-film-{index}.mkv",
                    )
                )
            else:
                for episode in range(1, 6):
                    db.add(
                        MediaItem(
                            media_id=media.id,
                            season=1,
                            episode=episode,
                            title=f"Épisode {episode}",
                            air_date=date(2024, 1, episode),
                            downloaded=True,
                            watched=True,
                            watched_at=datetime.now(timezone.utc),
                            state="downloaded",
                            file_path=f"/fiction/hive-serie-{index}-s01e{episode:02d}.mkv",
                        )
                    )
            media_ids.append(media.id)
        db.commit()
    finally:
        db.close()

    def _count_queries(engine):
        queries: list[str] = []

        def before(_conn, _cursor, statement, _parameters, _context, _executemany):
            queries.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        return queries, before

    small_queries, small_hook = _count_queries(app.state.engine)
    try:
        small = client.get(f"/api/v1/hive/media/sharing-summary?ids={','.join(str(i) for i in media_ids[:5])}")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", small_hook)

    large_queries, large_hook = _count_queries(app.state.engine)
    try:
        large = client.get(f"/api/v1/hive/media/sharing-summary?ids={','.join(str(i) for i in media_ids)}")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", large_hook)

    assert small.status_code == 200, small.text
    assert large.status_code == 200, large.text
    assert len(large.json()) == len(media_ids)
    assert len(large_queries) <= 18
    assert abs(len(large_queries) - len(small_queries)) <= 3


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
