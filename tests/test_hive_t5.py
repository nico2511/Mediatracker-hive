"""Hive T5 : transfert signé, Range, reprise, bascule, quota, tirage."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes, csrf, login
from tests.test_hive_t3_peer_http import _enable, _settings_pair

from mediatracker.api.app import create_app
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import (
    HiveEntry,
    HiveFileFingerprint,
    HiveOriginReachability,
    HiveRuche,
    Media,
)
from mediatracker.hive.chunk_digest import file_sha256_hex
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.hive.transfer_sources import rank_sources
from mediatracker.hive.transfer_throughput import record_transfer_sample

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
URL_A = "https://mtr-a.exemple.fr"
URL_B = "https://mtr-b.exemple.fr"


@pytest.fixture
def transfer_pair(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "xfer_a"))
    app_b = create_app(_settings_pair(tmp_path, "xfer_b"))
    for app in (app_a, app_b):
        app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        app.state.hive_send_bandwidth = TransferBandwidthLimiter(bps=0)
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()
        set_peer_asgi_apps_for_tests(None)


def _join_existing_ruche(client_a, client_b, rid_hex: str, *, invitee_url: str = ""):
    login(client_a)
    rid = rid_hex
    from tests.test_hive_t3_peer_http import _create_invitation

    link = _create_invitation(client_a, rid)
    creator_id = client_a.get("/api/v1/hive/identity").json()["instance_id"]
    set_peer_asgi_apps_for_tests({URL_A: client_a.app})
    login(client_b)
    begun = client_b.post(
        "/api/v1/hive/join/begin",
        json={"link": link, "invitee_name": "bb", "invitee_public_address": invitee_url},
        headers=csrf(client_b),
    )
    assert begun.status_code == 200, begun.text
    pending_id = begun.json()["pending_id"]
    login(client_a)
    assert (
        client_a.post("/api/v1/hive/join/accept", json={"pending_id": pending_id}, headers=csrf(client_a)).status_code
        == 204
    )
    set_peer_asgi_apps_for_tests({URL_A: client_a.app})
    confirmed = client_b.post(
        "/api/v1/hive/join/confirm",
        json={"ruche_id_hex": rid, "creator_base_url": URL_A, "creator_instance_id": creator_id},
        headers=csrf(client_b),
    )
    assert confirmed.status_code == 200, confirmed.text
    db_a = client_a.app.state.session_factory()
    try:
        from mediatracker.hive.access import is_active_member

        invitee_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        assert is_active_member(db_a, ruche_id=bytes.fromhex(rid), instance_id=invitee_id)
    finally:
        db_a.close()
    return bytes.fromhex(rid)


def _join_ruche(client_a, client_b, *, invitee_url: str = ""):
    login(client_a)
    rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
    return _join_existing_ruche(client_a, client_b, rid, invitee_url=invitee_url)


def _seed_file_on_a(app_a, ruche_id: bytes, payload: bytes) -> tuple[str, str, str]:
    settings = app_a.state.settings
    pk = load_private_key(settings)
    assert pk
    origin = hive_public_id_from_public_key(pk.public_key())
    db = app_a.state.session_factory()
    try:
        layout = storage_layout(db, settings)
        folder = layout.movies_dir / "Demo (2020) [tmdbid-9001]"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "demo.bin"
        video.write_bytes(payload)
        media = Media(type="movie", title="Demo", year=2020, tmdb_id=9001)
        db.add(media)
        db.flush()
        from mediatracker.db.models import MediaItem

        item = MediaItem(media_id=media.id, state="downloaded", downloaded=True, file_path=str(video.resolve()))
        db.add(item)
        db.flush()
        from mediatracker.library.scanner import remember_filed

        remember_filed(db, video.resolve(), media, item, NOW)
        sha = file_sha256_hex(video)
        fp = HiveFileFingerprint(
            media_item_id=item.id,
            path=str(video.resolve()),
            size=len(payload),
            mtime_ns=video.stat().st_mtime_ns,
            sha256_hex=sha,
            partial_size=len(payload),
            updated_at=NOW,
        )
        db.merge(fp)
        db.flush()
        from mediatracker.db.models import HiveShareIntent

        db.merge(HiveShareIntent(media_item_id=item.id))
        from mediatracker.hive.catalog import publish_available

        publish_available(db, settings, ruche_id=ruche_id, media=media, item=item, fingerprint_hex=sha, now=NOW)
        db.flush()
        from tests.hive_chunk_digest_testutil import persist_chunk_digests_for_test_file

        row = db.get(HiveFileFingerprint, item.id)
        assert row is not None
        persist_chunk_digests_for_test_file(db, row)
        db.commit()
        entry = db.scalar(
            select(HiveEntry).where(HiveEntry.ruche_id == ruche_id, HiveEntry.origin_instance_id == origin).limit(1)
        )
        assert entry and entry.file_id
        return origin, entry.file_id.hex(), fp.sha256_hex
    finally:
        db.close()


def test_range_and_auth_serve(transfer_pair):
    app_a, app_b = transfer_pair
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_B)
        rid = _join_ruche(ca, cb)
        blob = b"ABCDEFGH" * 1024
        _, fid, sha = _seed_file_on_a(app_a, rid, blob)
        set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
        pk_a = load_private_key(app_a.state.settings)
        path = f"/hive/files/{fid}"
        headers = sign_request_headers(private_key=pk_a, method="GET", path=path, query="", ruche_id=rid, body=b"")
        headers["Range"] = "bytes=0-4095"
        resp = ca.get(path, headers={**headers, "Range": "bytes=0-99"})
        assert resp.status_code == 206, resp.text
        assert resp.headers.get("Hive-Content-Sha256") == sha
        assert len(resp.content) == 100


def test_wrong_ruche_file_id_404(transfer_pair):
    app_a, app_b = transfer_pair
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_B)
        _join_ruche(ca, cb)
        other = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        path = "/hive/files/" + ("cd" * 16)
        pk_a = load_private_key(app_a.state.settings)
        headers = sign_request_headers(
            private_key=pk_a, method="GET", path=path, query="", ruche_id=bytes.fromhex(other), body=b""
        )
        assert ca.get(path, headers=headers).status_code == 404


def test_tirage_does_not_serve(transfer_pair):
    app_a, app_b = transfer_pair
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_B)
        rid = _join_ruche(ca, cb)
        login(ca)
        ca.patch(
            "/api/v1/settings",
            json={"values": {"hive_public_address": ""}},
            headers=csrf(ca),
        )
        min_video = catalog_min_logical_video_bytes()
        blob = b"z" * min_video
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        set_peer_asgi_apps_for_tests({URL_A: app_a})
        pk_b = load_private_key(app_b.state.settings)
        path = f"/hive/files/{fid}"
        headers = sign_request_headers(private_key=pk_b, method="GET", path=path, query="", ruche_id=rid, body=b"")
        assert ca.get(path, headers=headers).status_code == 404


def test_quota_closed_window(transfer_pair, monkeypatch):
    app_a, app_b = transfer_pair
    closed = NOW.replace(hour=3, minute=0)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_B)
        rid = _join_ruche(ca, cb)
        monkeypatch.setattr("mediatracker.api.hive_router._now", lambda: closed)
        min_video = catalog_min_logical_video_bytes()
        blob = b"R" * min_video
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        db_a = app_a.state.session_factory()
        try:
            from mediatracker.db.models import Setting

            db_a.merge(Setting(key="hive_transfer_window_start", value="0"))
            db_a.merge(Setting(key="hive_transfer_window_end", value="1"))
            db_a.commit()
        finally:
            db_a.close()
        pk_b = load_private_key(app_b.state.settings)
        path = f"/hive/files/{fid}"
        headers = sign_request_headers(
            private_key=pk_b, method="GET", path=path, query="", ruche_id=rid, body=b"", when=closed
        )
        assert ca.get(path, headers=headers).status_code == 429


def test_rate_limit_per_key_not_ip():
    lim = TransferBandwidthLimiter(bps=5_000)
    clock = {"t": 0.0}

    def tick():
        clock["t"] += 0.2
        return clock["t"]

    lim.set_clock(tick)
    lim.reserve("peer-a", 3_000)
    lim.reserve("peer-b", 3_000)
    assert lim._balance["peer-a"] == 7_000
    assert lim._balance["peer-b"] == 7_000


def test_record_sample_ignores_zero_duration(tmp_path):
    app = create_app(_settings_pair(tmp_path, "bps-ignore"))
    try:
        with TestClient(app):
            pass
        db = app.state.session_factory()
        rid = b"\x01" * 16
        db.add(HiveRuche(ruche_id=rid, creator_instance_id="hv1aa"))
        db.flush()
        db.add(HiveOriginReachability(ruche_id=rid, origin_instance_id="hv1aa", last_ok_at=NOW))
        db.commit()
        record_transfer_sample(db, ruche_id=rid, origin_instance_id="hv1aa", nbytes=1000, duration_seconds=0.0, now=NOW)
        db.commit()
        row = db.get(HiveOriginReachability, (rid, "hv1aa"))
        assert row.measured_bps is None
    finally:
        app.state.engine.dispose()


def test_rank_sources_prefers_measured_bps(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x02" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.flush()
        db.add(HiveOriginReachability(ruche_id=rid, origin_instance_id="slow", last_ok_at=NOW, measured_bps=100.0))
        db.add(HiveOriginReachability(ruche_id=rid, origin_instance_id="fast", last_ok_at=NOW, measured_bps=500.0))
        db.commit()
        sources = [
            {"ruche_id_hex": rid.hex(), "origine": "slow", "file_id_hex": "aa" * 16, "sha256": "s" * 64},
            {"ruche_id_hex": rid.hex(), "origine": "fast", "file_id_hex": "bb" * 16, "sha256": "s" * 64},
        ]
        ordered = rank_sources(db, sources, rng=random.Random(0))
        assert ordered[0]["origine"] == "fast"
        assert ordered[1]["origine"] == "slow"
    finally:
        db.close()


def test_rank_sources_unknown_bps_shuffled(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x03" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.commit()
        sources = [
            {"ruche_id_hex": rid.hex(), "origine": "x", "file_id_hex": "11" * 16},
            {"ruche_id_hex": rid.hex(), "origine": "y", "file_id_hex": "22" * 16},
        ]
        a = rank_sources(db, sources, rng=random.Random(1))
        b = rank_sources(db, sources, rng=random.Random(99))
        assert {s["origine"] for s in a} == {"x", "y"}
        assert [s["origine"] for s in a] != [s["origine"] for s in b] or len(sources) == 1
    finally:
        db.close()

