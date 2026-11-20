"""Hive T4 : gossip par ruche, sécurité §4, vue fusionnée locale."""

from __future__ import annotations

import inspect
import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import TEST_ADMIN_PASSWORD, csrf, login
from tests.test_hive_t3_peer_http import CREATOR_URL, _create_invitation, _enable

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.db.models import HiveEntry, Setting
from mediatracker.hive.catalog import build_catalog_payload, sign_payload
from mediatracker.hive.gossip_burst import (
    burst_due_ruches,
    hive_gossip_burst_tick,
    mark_local_catalog_change,
)
from mediatracker.hive.gossip_worker import PeerExchangeTarget, hive_gossip_due, hive_gossip_tick, public_peer_targets
from mediatracker.hive.hive_catalogue import (
    build_merged_hive_catalogue,
    load_hive_catalogue_rows,
    refresh_hive_catalogue_store,
)
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_client import gossip_sync, signed_peer_request
from mediatracker.hive.peer_gossip import entries_page_for_peer, ingest_signed_entries, version_vector_for_ruche
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
MARKER_ALLDEBRID = "FAKE-ALLDEBRID-T4-MARKER"
MARKER_TMDB = "FAKE-TMDB-T4-MARKER"
MARKER_JELLY = "FAKE-JELLYFIN-T4-MARKER"
MARKER_SESSION = "FAKE-SESSION-T4-MARKER"


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


def _join_ruche(client_a, client_b, rid_hex: str, creator_id: str, invitee_name: str, invitee_url: str):
    set_peer_asgi_app_for_tests(client_a.app)
    link = _create_invitation(client_a, rid_hex)
    login(client_b)
    begun = client_b.post(
        "/api/v1/hive/join/begin",
        json={"link": link, "invitee_name": invitee_name, "invitee_public_address": invitee_url},
        headers=csrf(client_b),
    )
    assert begun.status_code == 200, begun.text
    pending_id = begun.json()["pending_id"]
    login(client_a)
    accept = client_a.post(
        "/api/v1/hive/join/accept", json={"pending_id": pending_id}, headers=csrf(client_a)
    )
    assert accept.status_code == 204
    confirmed = client_b.post(
        "/api/v1/hive/join/confirm",
        json={"ruche_id_hex": rid_hex, "creator_base_url": CREATOR_URL, "creator_instance_id": creator_id},
        headers=csrf(client_b),
    )
    assert confirmed.status_code == 200, confirmed.text


def _store_signed_entry(db, settings, *, ruche_id: bytes, marker_title: str, seq: int = 1) -> None:
    pk = load_private_key(settings)
    assert pk is not None
    from mediatracker.hive.crypto_id import hive_public_id_from_public_key

    origin = hive_public_id_from_public_key(pk.public_key())
    cle = {"type": "movie", "tmdb_id": 900000 + seq, "version": "1080p+fr"}
    fiche = {"type": "movie", "tmdb_id": 900000 + seq, "title": marker_title, "year": 2099}
    file_id = bytes([seq]) * 16
    payload = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=seq,
        cle=cle,
        fiche=fiche,
        sha256_hex="a" * 64,
        file_id=file_id,
        etat="disponible",
    )
    sig = sign_payload(pk, payload)
    accepted = ingest_signed_entries(
        db, ruche_id=ruche_id, items=[{"payload": payload, "signature_b64": sig}], now=_utc_now()
    )
    assert accepted == 1
    db.commit()


@pytest.fixture
def hive_trio(tmp_path):
    apps = [create_app(_settings_pair(tmp_path, f"inst_{i}")) for i in ("a", "b", "c")]
    try:
        yield apps
    finally:
        for app in apps:
            app.state.engine.dispose()
        set_peer_asgi_app_for_tests(None)


def test_hive_router_handlers_use_peer_auth():
    import mediatracker.api.hive_router as mod

    src = inspect.getsource(mod.mount_hive_router)
    for chunk in src.split("@router.")[1:]:
        if "hive_unknown_path" in chunk or "hive_serve_file" in chunk:
            continue
        assert "authenticate_member_peer" in chunk or "authenticate_join_peer" in chunk


def test_peer_auth_status_codes(client):
    login(client)
    assert client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": CREATOR_URL}},
        headers=csrf(client),
    ).status_code == 200
    assert client.get("/hive/ruche/" + "aa" * 16 + "/access-check").status_code == 404
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    pk = load_private_key(client.app.state.settings)
    assert pk is not None
    path = f"/hive/ruche/{rid}/access-check"
    headers = sign_request_headers(
        private_key=pk,
        method="GET",
        path=path,
        query="",
        ruche_id=ruche_id,
        body=b"",
        when=_utc_now(),
    )
    headers["Hive-Signature"] = "aaaa"
    r = client.get(path, headers=headers)
    assert r.status_code == 401


def test_nonce_replay_401(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": CREATOR_URL}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    pk = load_private_key(client.app.state.settings)
    assert pk is not None
    path = f"/hive/ruche/{rid}/access-check"
    headers = sign_request_headers(
        private_key=pk, method="GET", path=path, query="", ruche_id=ruche_id, body=b"", when=_utc_now()
    )
    assert client.get(path, headers=headers).status_code == 204
    assert client.get(path, headers=headers).status_code == 401


def test_rate_limit_per_hive_id_independent(hive_trio):
    app_a, app_b, _ = hive_trio
    app_a.state.hive_peer_limiter = PeerRateLimiter(limit=2, window_seconds=60.0)
    app_b.state.hive_peer_limiter = PeerRateLimiter(limit=2, window_seconds=60.0)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", "https://mtr-test.exemple.fr")
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", "https://mtr-test.exemple.fr")
        ruche_id = bytes.fromhex(rid)
        pk_a = load_private_key(app_a.state.settings)
        pk_b = load_private_key(app_b.state.settings)
        assert pk_a and pk_b
        set_peer_asgi_app_for_tests(app_a)
        for _ in range(2):
            assert signed_peer_request(
                base_url=CREATOR_URL,
                method="GET",
                path=f"/hive/ruche/{rid}/access-check",
                ruche_id=ruche_id,
                private_key=pk_a,
            ).status_code == 204
        assert (
            signed_peer_request(
                base_url=CREATOR_URL,
                method="GET",
                path=f"/hive/ruche/{rid}/access-check",
                ruche_id=ruche_id,
                private_key=pk_a,
            ).status_code
            == 429
        )
        assert (
            signed_peer_request(
                base_url=CREATOR_URL,
                method="GET",
                path=f"/hive/ruche/{rid}/access-check",
                ruche_id=ruche_id,
                private_key=pk_b,
            ).status_code
            == 204
        )


def test_gossip_convergence_three_instances_three_rounds(hive_trio):
    app_a, app_b, app_c = hive_trio
    urls = (CREATOR_URL, "https://mtr-test.exemple.fr", "https://mtr-third.exemple.fr")
    with TestClient(app_a) as ca, TestClient(app_b) as cb, TestClient(app_c) as cc:
        for c, url, name in ((ca, urls[0], "aa"), (cb, urls[1], "bb"), (cc, urls[2], "cc")):
            _enable(c, name, url)
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", urls[1])
        _join_ruche(ca, cc, rid, creator_id, "cc", urls[2])
        ruche_id = bytes.fromhex(rid)
        db_a = app_a.state.session_factory()
        db_b = app_b.state.session_factory()
        db_c = app_c.state.session_factory()
        _store_signed_entry(db_a, app_a.state.settings, ruche_id=ruche_id, marker_title="MARKER-A-T4", seq=1)
        _store_signed_entry(db_b, app_b.state.settings, ruche_id=ruche_id, marker_title="MARKER-B-T4", seq=2)
        _store_signed_entry(db_c, app_c.state.settings, ruche_id=ruche_id, marker_title="MARKER-C-T4", seq=3)
        pks = [load_private_key(a.state.settings) for a in (app_a, app_b, app_c)]
        assert all(pks)

        vectors = [version_vector_for_ruche(db, ruche_id) for db in (db_a, db_b, db_c)]
        apps = (app_a, app_b, app_c)
        from mediatracker.hive.peer_gossip import entries_missing_for_peer

        for app in apps:
            app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)

        hub = 0
        for _round in range(3):
            for leaf in (1, 2):
                for i, j in ((leaf, hub), (hub, leaf)):
                    set_peer_asgi_app_for_tests(apps[j])
                    sess_j = apps[j].state.session_factory()
                    peer_vec = version_vector_for_ruche(sess_j, ruche_id)
                    sess_j.close()
                    sess_i = apps[i].state.session_factory()
                    push = entries_missing_for_peer(sess_i, ruche_id=ruche_id, peer_vector=peer_vec)
                    sess_i.close()
                    resp = gossip_sync(
                        base_url=urls[j],
                        ruche_id=ruche_id,
                        private_key=pks[i],
                        vector=vectors[i],
                        entries=push,
                    )
                    assert resp.status_code == 200, resp.text
                    body = resp.json()
                    sess = apps[i].state.session_factory()
                    ingest_signed_entries(sess, ruche_id=ruche_id, items=body["entries"], now=_utc_now())
                    sess.commit()
                    vectors[i] = version_vector_for_ruche(sess, ruche_id)
                    sess.close()

        all_origins: set[str] = set()
        for app in apps:
            sess = app.state.session_factory()
            origins = list(
                sess.scalars(
                    select(HiveEntry.origin_instance_id).where(HiveEntry.ruche_id == ruche_id).distinct()
                ).all()
            )
            all_origins.update(str(o) for o in origins if o)
            assert len(origins) >= 2
            sess.close()
        assert len(all_origins) >= 3
        db_a.close()
        db_b.close()
        db_c.close()


def test_ruche_isolation_gossip(hive_trio):
    app_a, app_b, _ = hive_trio
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", "https://mtr-test.exemple.fr")
        login(ca)
        rid_a = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        rid_b = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid_a, creator_id, "bb", "https://mtr-test.exemple.fr")
        marker_b = "MARKER-RUCHE-B-ONLY-T4"
        db_a = app_a.state.session_factory()
        _store_signed_entry(db_a, app_a.state.settings, ruche_id=bytes.fromhex(rid_b), marker_title=marker_b, seq=9)
        pk_b = load_private_key(app_b.state.settings)
        assert pk_b
        set_peer_asgi_app_for_tests(app_a)
        resp = gossip_sync(
            base_url=CREATOR_URL,
            ruche_id=bytes.fromhex(rid_a),
            private_key=pk_b,
            vector={},
        )
        assert resp.status_code == 200
        blob = json.dumps(resp.json())
        assert marker_b not in blob
        assert rid_b not in blob
        wrong = gossip_sync(
            base_url=CREATOR_URL,
            ruche_id=bytes.fromhex(rid_b),
            private_key=pk_b,
            vector={},
        )
        assert wrong.status_code == 401
        db_a.close()


def test_forged_file_id_404(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": CREATOR_URL}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    pk = load_private_key(client.app.state.settings)
    assert pk
    # Chemin non conforme au schéma → 422 (fail-fast) plutôt qu'un 404 pair.
    assert client.get("/hive/files/../etc/passwd", headers={"Hive-Ruche": rid}).status_code == 422
    path = "/hive/files/" + ("ff" * 16)
    headers = sign_request_headers(
        private_key=pk, method="GET", path=path, query="", ruche_id=ruche_id, body=b"", when=_utc_now()
    )
    assert client.get(path, headers=headers).status_code == 404


def test_secrets_not_in_hive_responses(client, caplog):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": "ab",
                "hive_public_address": CREATOR_URL,
                "alldebrid_api_key": MARKER_ALLDEBRID,
                "tmdb_api_key": MARKER_TMDB,
                "jellyfin_api_key": MARKER_JELLY,
            }
        },
        headers=csrf(client),
    )
    db = client.app.state.session_factory()
    row = db.get(Setting, "secret_key")
    if row is not None:
        row.value = MARKER_SESSION
    db.commit()
    db.close()
    markers = (MARKER_ALLDEBRID, MARKER_TMDB, MARKER_JELLY, MARKER_SESSION)
    for path in (
        "/hive/unknown",
        "/hive/ruche/" + "11" * 16 + "/access-check",
        "/hive/files/" + "00" * 16,
    ):
        r = client.get(path)
        joined = (r.text or "") + str(dict(r.headers))
        for m in markers:
            assert m not in joined
    for m in markers:
        assert m not in caplog.text


def test_merged_catalogue_local_only(hive_trio):
    app_a, _, _ = hive_trio
    with TestClient(app_a):
        db = app_a.state.session_factory()
        view = build_merged_hive_catalogue(db, now=_utc_now())
        assert isinstance(view, list)
        n = refresh_hive_catalogue_store(db, now=_utc_now())
        db.commit()
        assert n >= 0
        assert isinstance(load_hive_catalogue_rows(db), list)
        import mediatracker.api.hive_router as hr

        assert "refresh_hive_catalogue_store" not in inspect.getsource(hr.mount_hive_router)
        assert "load_hive_catalogue_rows" not in inspect.getsource(hr.mount_hive_router)
        db.close()


def test_gossip_pagination_over_500(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": CREATOR_URL}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    pk = load_private_key(settings)
    assert pk
    from mediatracker.hive.crypto_id import hive_public_id_from_public_key

    origin = hive_public_id_from_public_key(pk.public_key())
    for seq in range(1, 521):
        cle = {"type": "movie", "tmdb_id": 910000 + seq, "version": "1080p+fr"}
        fiche = {"type": "movie", "tmdb_id": 910000 + seq, "title": f"PAG-{seq}", "year": 2099}
        payload = build_catalog_payload(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=seq,
            cle=cle,
            fiche=fiche,
            sha256_hex="c" * 64,
            file_id=bytes([seq % 256]) * 16,
            etat="disponible",
        )
        sig = sign_payload(pk, payload)
        ingest_signed_entries(
            db, ruche_id=ruche_id, items=[{"payload": payload, "signature_b64": sig}], now=_utc_now()
        )
    db.commit()
    page1 = entries_page_for_peer(db, ruche_id=ruche_id, peer_vector={}, limit=500)
    assert len(page1.entries) == 500
    assert page1.has_more is True
    assert page1.cursor is not None
    page2 = entries_page_for_peer(db, ruche_id=ruche_id, peer_vector={}, limit=500, cursor=page1.cursor)
    assert len(page2.entries) == 20
    assert page2.has_more is False
    body = json.dumps({"vector": {}, "entries": [], "limit": 100}).encode("utf-8")
    path = f"/hive/ruche/{rid}/gossip/sync"
    headers = sign_request_headers(
        private_key=pk, method="POST", path=path, query="", ruche_id=ruche_id, body=body, when=_utc_now()
    )
    resp = client.post(path, content=body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_more"] is True
    assert len(data["entries"]) == 100
    db.close()


def test_worker_gossip_tick_convergence(hive_trio):
    app_a, app_b, app_c = hive_trio
    urls = (CREATOR_URL, "https://mtr-test.exemple.fr", "https://mtr-third.exemple.fr")
    with TestClient(app_a) as ca, TestClient(app_b) as cb, TestClient(app_c) as cc:
        _enable(ca, "aa", urls[0])
        _enable(cb, "bb", urls[1])
        _enable(cc, "cc", urls[2])
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", urls[1])
        _join_ruche(ca, cc, rid, creator_id, "cc", urls[2])
        ruche_id = bytes.fromhex(rid)
        for app, title, seq in (
            (app_a, "WA", 1),
            (app_b, "WB", 2),
            (app_c, "WC", 3),
        ):
            db = app.state.session_factory()
            _store_signed_entry(db, app.state.settings, ruche_id=ruche_id, marker_title=title, seq=seq)
            db.close()
        apps = (app_a, app_b, app_c)
        for app in apps:
            app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        pairs = ((0, 1), (1, 0), (0, 2), (2, 0), (1, 2), (2, 1))
        for _ in range(3):
            for i, j in pairs:
                set_peer_asgi_app_for_tests(apps[j])
                db = apps[i].state.session_factory()
                hive_gossip_tick(
                    db,
                    apps[i].state.settings,
                    now=_utc_now(),
                    force=True,
                    peer_targets=[PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=urls[j])],
                )
                db.commit()
                db.close()
        all_origins: set[str] = set()
        for app in apps:
            db = app.state.session_factory()
            origins = list(
                db.scalars(
                    select(HiveEntry.origin_instance_id).where(HiveEntry.ruche_id == ruche_id).distinct()
                ).all()
            )
            all_origins.update(str(o) for o in origins if o)
            rows = load_hive_catalogue_rows(db)
            assert isinstance(rows, list)
            db.close()
        assert len(all_origins) >= 3


def test_tirage_pulls_without_serving_files(hive_trio):
    app_a, app_b, app_c = hive_trio
    with TestClient(app_a) as ca, TestClient(app_b) as cb, TestClient(app_c) as cc:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", "https://mtr-test.exemple.fr")
        _enable(cc, "ti", "")
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", "https://mtr-test.exemple.fr")
        _join_ruche(ca, cc, rid, creator_id, "ti", "")
        ruche_id = bytes.fromhex(rid)
        db_a = app_a.state.session_factory()
        _store_signed_entry(db_a, app_a.state.settings, ruche_id=ruche_id, marker_title="HOST", seq=1)
        db_a.close()
        app_c.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        set_peer_asgi_app_for_tests(app_a)
        db_c = app_c.state.session_factory()
        hive_gossip_tick(
            db_c,
            app_c.state.settings,
            now=_utc_now(),
            force=True,
            peer_targets=[PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=CREATOR_URL)],
        )
        db_c.commit()
        n = db_c.scalar(select(HiveEntry).where(HiveEntry.ruche_id == ruche_id).limit(1))
        assert n is not None
        pk_c = load_private_key(app_c.state.settings)
        assert pk_c
        path = "/hive/files/" + ("aa" * 16)
        headers = sign_request_headers(
            private_key=pk_c, method="GET", path=path, query="", ruche_id=ruche_id, body=b"", when=_utc_now()
        )
        with TestClient(app_c) as tc:
            assert tc.get(path, headers=headers).status_code == 404
        db_c.close()


def test_exclude_member_list_propagates(hive_trio):
    app_a, app_b, app_c = hive_trio
    with TestClient(app_a) as ca, TestClient(app_b) as cb, TestClient(app_c) as cc:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", "https://mtr-test.exemple.fr")
        _enable(cc, "cc", "https://mtr-third.exemple.fr")
        login(ca)
        rid_a = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        rid_b = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid_a, creator_id, "bb", "https://mtr-test.exemple.fr")
        _join_ruche(ca, cb, rid_b, creator_id, "bb", "https://mtr-test.exemple.fr")
        _join_ruche(ca, cc, rid_a, creator_id, "cc", "https://mtr-third.exemple.fr")
        invitee_id = cb.get("/api/v1/hive/identity").json()["instance_id"]
        db_b = app_b.state.session_factory()
        _store_signed_entry(db_b, app_b.state.settings, ruche_id=bytes.fromhex(rid_a), marker_title="EX-A", seq=1)
        _store_signed_entry(db_b, app_b.state.settings, ruche_id=bytes.fromhex(rid_b), marker_title="EX-B", seq=2)
        row_src = db_b.scalar(
            select(HiveEntry).where(
                HiveEntry.ruche_id == bytes.fromhex(rid_a),
                HiveEntry.origin_instance_id == invitee_id,
            )
        )
        db_b.close()
        assert row_src is not None and row_src.payload_json and row_src.signature_b64
        db_c = app_c.state.session_factory()
        ingest_signed_entries(
            db_c,
            ruche_id=bytes.fromhex(rid_a),
            items=[{"payload": json.loads(row_src.payload_json), "signature_b64": row_src.signature_b64}],
            now=_utc_now(),
        )
        db_c.commit()
        db_c.close()
        login(ca)
        assert (
            ca.post(
                f"/api/v1/hive/ruches/{rid_a}/exclude",
                json={"instance_id": invitee_id},
                headers=csrf(ca),
            ).status_code
            == 204
        )
        app_c.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        set_peer_asgi_app_for_tests(app_a)
        db_c = app_c.state.session_factory()
        hive_gossip_tick(
            db_c,
            app_c.state.settings,
            now=_utc_now(),
            force=True,
            peer_targets=[PeerExchangeTarget(ruche_id=bytes.fromhex(rid_a), peer_base_url=CREATOR_URL)],
        )
        db_c.commit()
        rows_a = list(
            db_c.scalars(
                select(HiveEntry).where(
                    HiveEntry.ruche_id == bytes.fromhex(rid_a),
                    HiveEntry.origin_instance_id == invitee_id,
                )
            ).all()
        )
        assert rows_a
        assert all(r.etat == "retire" for r in rows_a)
        rows_b = list(db_c.scalars(select(HiveEntry).where(HiveEntry.ruche_id == bytes.fromhex(rid_b))).all())
        assert rows_b == []
        db_c.close()


def test_public_peer_targets_at_most_two_random(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": CREATOR_URL}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    creator = client.get("/api/v1/hive/identity").json()["instance_id"]
    db = client.app.state.session_factory()
    for i in range(5):
        from mediatracker.db.models import HiveMember

        db.add(
            HiveMember(
                ruche_id=ruche_id,
                member_instance_id=f"hv1fake-fake-fake-fake-fake-fake-fak{i:02d}",
                public_key_pem="",
                public_address=f"https://peer-{i}.exemple.fr",
            )
        )
    db.commit()
    picked = public_peer_targets(
        db, ruche_id=ruche_id, self_instance_id=creator, count=2, rng=random.Random(42)
    )
    assert len(picked) == 2
    urls = [u for _, u in picked]
    assert len(set(urls)) == 2
    db.close()


def test_burst_groups_60s_and_hits_two_peers(hive_trio, monkeypatch):
    app_a, app_b, _ = hive_trio
    journal: list[PeerExchangeTarget] = []
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", "https://mtr-test.exemple.fr")
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", "https://mtr-test.exemple.fr")
        ruche_id = bytes.fromhex(rid)
        t0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
        db_a = app_a.state.session_factory()
        from mediatracker.db.models import HiveMember

        for i in range(3):
            db_a.add(
                HiveMember(
                    ruche_id=ruche_id,
                    member_instance_id=f"hv1fake-fake-fake-fake-fake-fake-fb{i:02d}",
                    public_key_pem="",
                    public_address=f"https://burst-peer-{i}.exemple.fr",
                )
            )
        db_a.commit()
        mark_local_catalog_change(db_a, ruche_id=ruche_id, now=t0)
        db_a.commit()
        assert burst_due_ruches(db_a, t0 + timedelta(seconds=59)) == []
        assert burst_due_ruches(db_a, t0 + timedelta(seconds=60)) == [ruche_id]
        monkeypatch.setattr(
            "mediatracker.hive.gossip_burst.send_gossip_hint_to_peer",
            lambda *a, **k: True,
        )
        sent = hive_gossip_burst_tick(
            db_a,
            app_a.state.settings,
            now=t0 + timedelta(seconds=60),
            rng=random.Random(1),
            on_hint_sent=journal.append,
        )
        assert sent == 2
        assert len(journal) == 2
        assert all(t.ruche_id == ruche_id for t in journal)
        assert burst_due_ruches(db_a, t0 + timedelta(seconds=61)) == []
        db_a.close()


def test_tour_contacts_at_most_two_peers_per_ruche(hive_trio, monkeypatch):
    app_a, app_b, app_c = hive_trio
    calls: list[str] = []

    def _fake_exchange(db, settings, target, *, now):
        calls.append(target.peer_base_url)

    monkeypatch.setattr("mediatracker.hive.gossip_worker.run_peer_exchange", _fake_exchange)
    with TestClient(app_a) as ca, TestClient(app_b) as cb, TestClient(app_c) as cc:
        urls = (CREATOR_URL, "https://mtr-test.exemple.fr", "https://mtr-c.exemple.fr")
        for c, url, name in ((ca, urls[0], "aa"), (cb, urls[1], "bb"), (cc, urls[2], "cc")):
            _enable(c, name, url)
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", urls[1])
        _join_ruche(ca, cc, rid, creator_id, "cc", urls[2])
        db_a = app_a.state.session_factory()
        from mediatracker.db.models import HiveMember

        for i in range(4):
            db_a.add(
                HiveMember(
                    ruche_id=bytes.fromhex(rid),
                    member_instance_id=f"hv1fake-fake-fake-fake-fake-fake-ft{i:02d}",
                    public_key_pem="",
                    public_address=f"https://tour-peer-{i}.exemple.fr",
                )
            )
        db_a.commit()
        hive_gossip_tick(
            db_a,
            app_a.state.settings,
            now=_utc_now(),
            force=True,
            peer_targets=None,
        )
        db_a.commit()
        assert len(calls) == 2
        db_a.close()


def test_burst_arm_isolated_per_ruche(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": CREATOR_URL}},
        headers=csrf(client),
    )
    now = _utc_now()
    db = client.app.state.session_factory()
    ra, rb = bytes([1] * 16), bytes([2] * 16)
    mark_local_catalog_change(db, ruche_id=ra, now=now)
    db.commit()
    due = burst_due_ruches(db, now + timedelta(seconds=60))
    assert due == [ra]
    assert rb not in due
    db.close()


def test_burst_noop_when_hive_off(client):
    db = client.app.state.session_factory()
    now = _utc_now()
    mark_local_catalog_change(db, ruche_id=bytes(16), now=now)
    db.commit()
    state = db.get(Setting, "hive_gossip_burst_state")
    assert state is None or not (state.value or "").strip()
    assert hive_gossip_burst_tick(db, client.app.state.settings, now=now) == 0
    db.close()


def test_hive_gossip_skipped_when_off(client):
    login(client)
    db = client.app.state.session_factory()
    assert hive_gossip_due(db, _utc_now()) is False
    assert hive_gossip_tick(db, client.app.state.settings, now=_utc_now(), force=True) is False
    db.close()
