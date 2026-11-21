"""Quota pair membre : quota-for-peer, my-quota, notification."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import TEST_ADMIN_PASSWORD, csrf, login

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.db.models import Event, HiveReceiveQuota
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_client import signed_peer_request
from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests
from mediatracker.hive.transfer_settings import week_start_date

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
CREATOR_URL = "https://creator.mediatracker.example.com"
MEMBER_URL = "https://member.mediatracker.example.com"
USED = 12_000_000_000


def _freeze_hive_clock(monkeypatch, moment: datetime) -> None:
    from mediatracker.api import hive_router as hive_router_mod
    from mediatracker.api import v1 as v1_api
    from mediatracker.hive import peer_client

    monkeypatch.setattr(hive_router_mod, "_now", lambda: moment)
    monkeypatch.setattr(v1_api.hive_ruches, "_now", lambda: moment)
    _orig = peer_client.sign_request_headers

    def _signed(**kwargs):
        kwargs.setdefault("when", moment)
        return _orig(**kwargs)

    monkeypatch.setattr(peer_client, "sign_request_headers", _signed)


def _settings_pair(tmp_path, suffix: str) -> Settings:
    base = tmp_path / suffix
    base.mkdir(parents=True, exist_ok=True)
    return Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password=TEST_ADMIN_PASSWORD,
        database_url=f"sqlite:///{base / 'app.db'}",
        data_dir=base / "data",
        config_dir=base / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
    )


def _enable(client, name: str, public_address: str):
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": name,
                "hive_public_address": public_address,
            }
        },
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text


@pytest.fixture
def hive_pair(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "inst_a"))
    app_b = create_app(_settings_pair(tmp_path, "inst_b"))
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()
        set_peer_asgi_app_for_tests(None)


def _join_pair(client_a, client_b, app_a, app_b, *, invitee_name: str = "bb"):
    login(client_a)
    rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
    login(client_a)
    inv = client_a.post(
        "/api/v1/hive/ruches/invitations",
        json={"ruche_id_hex": rid},
        headers=csrf(client_a),
    )
    assert inv.status_code == 201, inv.text
    link = inv.json()["link"]
    creator_id = client_a.get("/api/v1/hive/identity").json()["instance_id"]
    set_peer_asgi_app_for_tests(app_a)
    login(client_b)
    begun = client_b.post(
        "/api/v1/hive/join/begin",
        json={"link": link, "invitee_name": invitee_name, "invitee_public_address": ""},
        headers=csrf(client_b),
    )
    assert begun.status_code == 200, begun.text
    pending_id = begun.json()["pending_id"]
    login(client_a)
    assert client_a.post(
        "/api/v1/hive/join/accept",
        json={"pending_id": pending_id},
        headers=csrf(client_a),
    ).status_code == 204
    confirmed = client_b.post(
        "/api/v1/hive/join/confirm",
        json={
            "ruche_id_hex": rid,
            "creator_base_url": CREATOR_URL,
            "creator_instance_id": creator_id,
        },
        headers=csrf(client_b),
    )
    assert confirmed.status_code == 200, confirmed.text
    set_peer_asgi_app_for_tests(app_a)
    member_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
    from mediatracker.db.models import HiveMember

    for app in (app_a, app_b):
        db = app.state.session_factory()
        try:
            row = db.scalar(
                select(HiveMember).where(
                    HiveMember.ruche_id == bytes.fromhex(rid),
                    HiveMember.member_instance_id == member_id,
                )
            )
            if row is not None:
                row.public_address = MEMBER_URL
                db.commit()
        finally:
            db.close()
    return rid, creator_id


def test_quota_for_peer_returns_only_requester(hive_pair, monkeypatch):
    from mediatracker.api import hive_router as hive_router_mod

    app_a, app_b = hive_pair
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        rid, _ = _join_pair(client_a, client_b, app_a, app_b)
        moment = datetime.now(timezone.utc)
        monkeypatch.setattr(hive_router_mod, "_now", lambda: moment)
        ruche_id = bytes.fromhex(rid)
        member_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        db = app_a.state.session_factory()
        try:
            ws = week_start_date(moment)
            db.merge(
                HiveReceiveQuota(
                    ruche_id=ruche_id,
                    member_instance_id=member_id,
                    week_start=ws,
                    bytes_used=USED,
                )
            )
            db.commit()
        finally:
            db.close()
        pk_b = load_private_key(app_b.state.settings)
        assert pk_b is not None
        path = f"/hive/ruche/{rid}/quota-for-peer"
        resp = signed_peer_request(
            base_url=CREATOR_URL,
            method="GET",
            path=path,
            ruche_id=ruche_id,
            private_key=pk_b,
            when=moment,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bytes_used"] == USED
        assert body["quota_bytes"] > 0
        assert body["source_display_name"] == "aa"
        assert "receive_bytes_used" not in body
        keys = set(body.keys())
        assert "member_instance_id" not in keys


def test_my_quota_marks_unavailable_source(hive_pair, monkeypatch):
    from mediatracker.api import v1 as v1_api

    app_a, app_b = hive_pair
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        rid, _ = _join_pair(client_a, client_b, app_a, app_b)
        monkeypatch.setattr(v1_api.hive_ruches, "_now", lambda: NOW)
        set_peer_asgi_app_for_tests(app_a)

        def _silent(**kw):
            return {
                "status": "unavailable",
                "source_instance_id": kw.get("source_instance_id") or "",
                "source_display_name": kw.get("source_display_name") or "",
            }

        with patch("mediatracker.hive.quota_peer._fetch_one_quota", side_effect=_silent):
            login(client_b)
            r = client_b.get(f"/api/v1/hive/ruches/{rid}/my-quota", headers=csrf(client_b))
        assert r.status_code == 200, r.text
        sources = r.json()["sources"]
        assert len(sources) >= 1
        assert all(s.get("status") == "unavailable" for s in sources)


def test_member_notified_after_owner_reset(hive_pair, monkeypatch):
    app_a, app_b = hive_pair
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        rid, _ = _join_pair(client_a, client_b, app_a, app_b)
        _freeze_hive_clock(monkeypatch, NOW)
        member_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        db_a = app_a.state.session_factory()
        try:
            ws = week_start_date(NOW)
            db_a.merge(
                HiveReceiveQuota(
                    ruche_id=bytes.fromhex(rid),
                    member_instance_id=member_id,
                    week_start=ws,
                    bytes_used=USED,
                )
            )
            db_a.commit()
        finally:
            db_a.close()
        login(client_a)
        r = client_a.post(
            f"/api/v1/hive/ruches/{rid}/members/{member_id}/quota/reset",
            headers=csrf(client_a),
        )
        assert r.status_code == 204, r.text
        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r2 = client_b.get(f"/api/v1/hive/ruches/{rid}/my-quota", headers=csrf(client_b))
        assert r2.status_code == 200, r2.text
        db_b = app_b.state.session_factory()
        try:
            evt = db_b.scalar(
                select(Event)
                .where(Event.type == "hive.quota.reset.notice")
                .order_by(Event.id.desc())
                .limit(1)
            )
            assert evt is not None
            msg = str((evt.payload or {}).get("message") or "")
            assert "remis à zéro" in msg.lower()
            assert "aa" in msg
        finally:
            db_b.close()


def test_quota_reset_journal_uses_display_name(hive_pair, monkeypatch):
    app_a, app_b = hive_pair
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        rid, _ = _join_pair(client_a, client_b, app_a, app_b)
        _freeze_hive_clock(monkeypatch, NOW)
        member_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        db = app_a.state.session_factory()
        try:
            ws = week_start_date(NOW)
            db.merge(
                HiveReceiveQuota(
                    ruche_id=bytes.fromhex(rid),
                    member_instance_id=member_id,
                    week_start=ws,
                    bytes_used=USED,
                )
            )
            db.commit()
        finally:
            db.close()
        login(client_a)
        r = client_a.post(
            f"/api/v1/hive/ruches/{rid}/members/{member_id}/quota/reset",
            headers=csrf(client_a),
        )
        assert r.status_code == 204, r.text
        db = app_a.state.session_factory()
        try:
            evt = db.scalar(select(Event).where(Event.type == "hive.quota.reset").order_by(Event.id.desc()).limit(1))
            assert evt is not None
            message = str((evt.payload or {}).get("message") or "")
            assert "bb" in message
            assert member_id[:8] not in message.split("(")[0]
        finally:
            db.close()


def test_my_quota_deadline_under_three_seconds(hive_pair, monkeypatch):
    import time

    from mediatracker.hive.quota_peer import MY_QUOTA_PEER_TIMEOUT_S, aggregate_my_quota

    app_a, app_b = hive_pair
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        rid, _ = _join_pair(client_a, client_b, app_a, app_b)
        _freeze_hive_clock(monkeypatch, NOW)

        def _slow(**_kw):
            time.sleep(MY_QUOTA_PEER_TIMEOUT_S + 1.5)
            return {"status": "ok", "source_instance_id": "x", "source_display_name": "y"}

        with patch("mediatracker.hive.quota_peer._fetch_one_quota", side_effect=_slow):
            db = app_b.state.session_factory()
            try:
                t0 = time.monotonic()
                raw = aggregate_my_quota(
                    db, app_b.state.settings, ruche_id=bytes.fromhex(rid), now=NOW
                )
                elapsed = time.monotonic() - t0
            finally:
                db.close()
        assert elapsed < MY_QUOTA_PEER_TIMEOUT_S + 0.75
        assert raw["sources"]
        assert all(s.get("status") == "unavailable" for s in raw["sources"])


def test_my_quota_notice_single_event_on_two_fetches(hive_pair, monkeypatch):
    from mediatracker.db.models import Setting
    from mediatracker.hive.quota_peer import HIVE_QUOTA_RESET_NOTICES_KEY

    app_a, app_b = hive_pair
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        rid, _ = _join_pair(client_a, client_b, app_a, app_b)
        _freeze_hive_clock(monkeypatch, NOW)
        member_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        db_a = app_a.state.session_factory()
        try:
            ws = week_start_date(NOW)
            db_a.merge(
                HiveReceiveQuota(
                    ruche_id=bytes.fromhex(rid),
                    member_instance_id=member_id,
                    week_start=ws,
                    bytes_used=USED,
                )
            )
            db_a.commit()
        finally:
            db_a.close()
        login(client_a)
        assert (
            client_a.post(
                f"/api/v1/hive/ruches/{rid}/members/{member_id}/quota/reset",
                headers=csrf(client_a),
            ).status_code
            == 204
        )
        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        path = f"/api/v1/hive/ruches/{rid}/my-quota"
        assert client_b.get(path, headers=csrf(client_b)).status_code == 200
        assert client_b.get(path, headers=csrf(client_b)).status_code == 200
        db_a = app_a.state.session_factory()
        try:
            row = db_a.get(Setting, HIVE_QUOTA_RESET_NOTICES_KEY)
            assert row is not None
            assert member_id in str(row.value)
        finally:
            db_a.close()
        db_b = app_b.state.session_factory()
        try:
            notices = list(
                db_b.scalars(select(Event).where(Event.type == "hive.quota.reset.notice")).all()
            )
            assert len(notices) == 1
        finally:
            db_b.close()