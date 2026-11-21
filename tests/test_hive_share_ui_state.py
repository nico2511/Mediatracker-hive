"""Contrat API partage fiche : has_file réel, provenance Hive, transferts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import PLAUSIBLE_VIDEO_BYTES, login, write_sparse_video_file
from tests.test_hive_t2 import _enable_hive

from mediatracker.db.models import Download, DownloadHiveFetch, HiveFetchGrant, Media, MediaItem

NOW = datetime.now(timezone.utc)


def test_sharing_has_file_uses_present_not_downloaded_flag(client, app, settings, tmp_path):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fiction", year=2099, tmdb_id=88101)
        db.add(media)
        db.flush()
        ghost = tmp_path / "ghost.mkv"
        write_sparse_video_file(ghost, PLAUSIBLE_VIDEO_BYTES)
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(ghost))
        db.add(item)
        db.commit()
        item_id = item.id
        ghost.unlink()
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["has_file"] is False


def test_sharing_has_file_present_on_disk(client, app, settings, tmp_path):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fiction", year=2099, tmdb_id=88102)
        db.add(media)
        db.flush()
        video = tmp_path / "f-88102.mkv"
        write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.commit()
        item_id = item.id
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["has_file"] is True


def test_received_via_hive_after_hive_download(client, app, settings, tmp_path):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fiction", year=2099, tmdb_id=88103)
        db.add(media)
        db.flush()
        video = tmp_path / "hive-88103.mkv"
        write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        dl = Download(media_item_id=item.id, state="done", progress=100, created_at=NOW, finished_at=NOW)
        db.add(dl)
        db.flush()
        db.add(
            DownloadHiveFetch(
                download_id=dl.id,
                sha256_hex="a" * 64,
                sources_json="[]",
                ruche_id=bytes([0xAA] * 16),
                file_id=bytes([0xBB] * 16),
                origin_instance_id="origin-test",
                peer_base_url="https://peer.example.invalid",
                expected_size=PLAUSIBLE_VIDEO_BYTES,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["received_via_hive"] is True


def test_outbound_transfer_fields_present(client, app, settings, tmp_path):
    from tests.test_hive_catalog_sharing_worker import _join_ruche
    from tests.test_hive_t2 import _origin_id

    login(client)
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xEE] * 15 + [0xEE])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        media = Media(type="film", title="Fiction", year=2099, tmdb_id=88104)
        db.add(media)
        db.flush()
        video = tmp_path / "out-88104.mkv"
        write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES)
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        db.add(
            HiveFetchGrant(
                grant_sha256_hex="b" * 64,
                ruche_id=ruche_id,
                file_id=bytes([0x11] * 16),
                requester_instance_id="peer-test",
                media_item_id=item.id,
                content_size=PLAUSIBLE_VIDEO_BYTES,
                content_mtime_ns=0,
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        db.commit()
        item_id = item.id
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/{item_id}/sharing").json()
    assert body["transfer_outbound_active"] is True


def test_batch_sharing_endpoint_returns_list(client, app, settings):
    login(client)
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Fiction", year=2098, tmdb_id=88210)
        db.add(media)
        db.flush()
        ids: list[int] = []
        for number in (1, 2):
            item = MediaItem(media_id=media.id, season=1, episode=number)
            db.add(item)
            db.flush()
            assert item.id is not None
            ids.append(item.id)
        db.commit()
    finally:
        db.close()
    body = client.get(f"/api/v1/hive/items/sharing?ids={ids[0]},{ids[1]}").json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert {row["media_item_id"] for row in body} == set(ids)


def test_batch_share_ui_fields_query_count_stable_for_200_episodes(client, app):
    from sqlalchemy import event, select

    from mediatracker.hive.share_ui_state import batch_share_ui_fields

    del client

    def seed(count: int, tmdb_id: int) -> list[int]:
        db = app.state.session_factory()
        try:
            media = Media(type="series", title="Fiction", year=2098, tmdb_id=tmdb_id)
            db.add(media)
            db.flush()
            ids: list[int] = []
            for number in range(1, count + 1):
                item = MediaItem(media_id=media.id, season=1, episode=number)
                db.add(item)
                db.flush()
                assert item.id is not None
                ids.append(item.id)
            db.commit()
            return ids
        finally:
            db.close()

    def count_queries(item_ids: list[int]) -> int:
        queries: list[str] = []

        def before(_conn, _cursor, statement, _parameters, _context, _executemany):
            queries.append(statement)

        engine = app.state.engine
        event.listen(engine, "before_cursor_execute", before)
        try:
            db = app.state.session_factory()
            try:
                items = list(db.scalars(select(MediaItem).where(MediaItem.id.in_(item_ids))).all())
                queries.clear()
                batch_share_ui_fields(db, items, sha_by_item={}, now=NOW, parallel_gate=None)
            finally:
                db.close()
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(queries)

    small_ids = seed(20, 88211)
    large_ids = seed(200, 88212)
    small = count_queries(small_ids)
    large = count_queries(large_ids)
    assert small > 0
    assert large <= small + 2


def test_transfer_parallel_gate_tracks_bytes_and_clears_on_release():
    from mediatracker.hive.transfer_parallel import TransferParallelGate

    gate = TransferParallelGate()
    assert gate.acquire(7, limit=2)
    gate.record_grant_bytes(7, 250)
    gate.record_grant_bytes(7, 250)
    assert gate.transfer_pct_for_grant(7, 1000) == 50.0
    assert gate.acquire(7, limit=2)
    gate.release(7)
    assert gate.is_grant_active(7)
    assert gate.transfer_pct_for_grant(7, 1000) == 50.0
    gate.release(7)
    assert not gate.is_grant_active(7)
    assert gate.transfer_pct_for_grant(7, 1000) is None


def test_transfer_parallel_gate_concurrent_byte_records():
    import threading

    from mediatracker.hive.transfer_parallel import TransferParallelGate

    gate = TransferParallelGate()
    assert gate.acquire(1, limit=4)
    def work() -> None:
        for _ in range(1000):
            gate.record_grant_bytes(1, 1)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert gate.transfer_pct_for_grant(1, 8000) == 100.0
    gate.release(1)
    assert gate.transfer_pct_for_grant(1, 8000) is None
