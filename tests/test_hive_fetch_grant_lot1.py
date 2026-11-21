"""LOT1 : jeton d'accès fichier temporaire (émetteur Hive)."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes, csrf, login
from tests.test_hive_t3_peer_http import _enable
from tests.test_hive_t5 import NOW, URL_A, URL_B, _join_ruche, _seed_file_on_a

from mediatracker.db.models import HiveEntry, HiveFetchGrant, HiveFileFingerprint, Setting
from mediatracker.hive.chunk_digest import TRANSFER_CHUNK_SIZE
from mediatracker.hive.fetch_grant import HIVE_FETCH_GRANT_HEADER, FetchGrantDenied, mint_fetch_grant
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.membership import exclude_member
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests
from mediatracker.hive.share_exclusions import replace_share_exclusions
from mediatracker.hive.share_policy import revoke_share_intent

pytest_plugins = ["tests.test_hive_t5"]


def _mint_path(rid: bytes, fid_hex: str) -> str:
    return f"/hive/ruche/{rid.hex()}/files/{fid_hex}/fetch-grant"


def _file_path(fid_hex: str) -> str:
    return f"/hive/files/{fid_hex}"


def _sign_mint(signer_app, rid: bytes, fid_hex: str):
    path = _mint_path(rid, fid_hex)
    pk = load_private_key(signer_app.state.settings)
    assert pk
    headers = sign_request_headers(
        private_key=pk, method="POST", path=path, query="", ruche_id=rid, body=b""
    )
    return path, headers


def _mint(emitter_client: TestClient, signer_app, rid: bytes, fid_hex: str) -> str:
    path, headers = _sign_mint(signer_app, rid, fid_hex)
    resp = emitter_client.post(path, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "grant" in body and isinstance(body["grant"], str) and len(body["grant"]) > 20
    assert "expires_at" in body
    return str(body["grant"])


def _clear_chunk_digests(app, payload_size: int) -> None:
    db = app.state.session_factory()
    try:
        fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == payload_size).limit(1))
        assert fp is not None
        fp.chunk_digests_json = None
        fp.chunk_digest_offset = 0
        fp.chunk_digests_sha256_hex = None
        db.commit()
    finally:
        db.close()


def _grant_headers(rid: bytes, grant: str, *, extra: dict | None = None) -> dict[str, str]:
    out = {"Hive-Ruche": rid.hex(), HIVE_FETCH_GRANT_HEADER: grant}
    if extra:
        out.update(extra)
    return out


def _setup_pair(app_a, app_b, ca, cb):
    _enable(ca, "aa", URL_A)
    _enable(cb, "bb", URL_B)
    rid = _join_ruche(ca, cb)
    set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
    return rid


def test_mint_replay_nonce_denied(transfer_pair):
    app_a, app_b = transfer_pair
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, b"nonce-mint-" * 64)
        path, headers = _sign_mint(app_b, rid, fid)
        assert ca.post(path, headers=headers).status_code == 200
        assert ca.post(path, headers=headers).status_code == 401


def test_fetch_grant_get_head_range_without_chunk_digests(transfer_pair):
    app_a, app_b = transfer_pair
    payload_size = TRANSFER_CHUNK_SIZE + 1024
    payload = os.urandom(payload_size)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, sha = _seed_file_on_a(app_a, rid, payload)
        _clear_chunk_digests(app_a, payload_size)
        grant = _mint(ca, app_b, rid, fid)
        fpath = _file_path(fid)
        mid = TRANSFER_CHUNK_SIZE
        end = payload_size - 1
        span = end - mid + 1
        resp = ca.get(fpath, headers=_grant_headers(rid, grant, extra={"Range": f"bytes={mid}-{end}"}))
        assert resp.status_code == 206, resp.text
        assert resp.headers.get("Hive-Content-Sha256") == sha
        assert "Hive-Chunk-Sha256" not in resp.headers
        assert len(resp.content) == span

        head = ca.head(fpath, headers=_grant_headers(rid, grant))
        assert head.status_code == 200, head.text
        assert head.headers.get("Hive-Content-Length") == str(payload_size)
        assert "Hive-Chunk-Sha256" not in head.headers
        assert head.content in (b"", None)


def test_fetch_missing_invalid_expired_grant(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"x" * 8192
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        fpath = _file_path(fid)
        assert ca.get(fpath, headers={"Hive-Ruche": rid.hex()}).status_code == 404
        bad = _grant_headers(rid, "not-a-valid-grant-token-0123456789abcdef")
        assert ca.get(fpath, headers=bad).status_code == 401
        grant = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            row = db.scalar(select(HiveFetchGrant).limit(1))
            assert row is not None
            row.expires_at = NOW - timedelta(minutes=5)
            db.commit()
        finally:
            db.close()
        assert ca.get(fpath, headers=_grant_headers(rid, grant)).status_code == 401


def test_fetch_wrong_file_or_ruche(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"y" * 4096
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        other_fid = "bb" * 16
        assert ca.get(_file_path(other_fid), headers=_grant_headers(rid, grant)).status_code == 401
        login(ca)
        other_rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        assert (
            ca.get(_file_path(fid), headers=_grant_headers(bytes.fromhex(other_rid), grant)).status_code == 401
        )


def test_fetch_denied_when_member_excluded(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"z" * 6000
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        fpath = _file_path(fid)
        hdrs = _grant_headers(rid, grant, extra={"Range": "bytes=0-99"})
        db = app_a.state.session_factory()
        try:
            invitee = cb.get("/api/v1/hive/identity").json()["instance_id"]
            exclude_member(db, app_a.state.settings, ruche_id=rid, target_instance_id=invitee, now=NOW)
            db.commit()
        finally:
            db.close()
        assert ca.get(fpath, headers=hdrs).status_code == 401


def test_fetch_denied_when_share_revoked(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"w" * 6000
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(blob)).limit(1))
            assert fp
            revoke_share_intent(db, fp.media_item_id)
            db.commit()
        finally:
            db.close()
        assert ca.get(_file_path(fid), headers=_grant_headers(rid, grant)).status_code == 401


def test_fetch_denied_when_ruche_excluded_for_item(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"excl-" * 1200
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(blob)).limit(1))
            assert fp
            replace_share_exclusions(db, fp.media_item_id, exclude_all=False, ruche_ids=[rid])
            db.commit()
        finally:
            db.close()
        assert ca.get(_file_path(fid), headers=_grant_headers(rid, grant)).status_code == 401


def test_fetch_rejects_changed_file_version(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"v1-" * 2000
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(blob)).limit(1))
            assert fp
            Path(fp.path).write_bytes(b"v2-replaced-" * 200)
            db.commit()
        finally:
            db.close()
        assert ca.get(_file_path(fid), headers=_grant_headers(rid, grant)).status_code == 401


def test_fetch_rejects_mtime_drift_without_size_change(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"mtime-" * 900
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(blob)).limit(1))
            assert fp
            path = Path(fp.path)
            st = path.stat()
            drift_ns = int(st.st_mtime_ns) + 5_000_000_000
            os.utime(path, ns=(drift_ns, drift_ns))
            db.commit()
        finally:
            db.close()
        assert ca.get(_file_path(fid), headers=_grant_headers(rid, grant)).status_code == 401


def test_error_body_never_echoes_grant_secret(transfer_pair):
    secret = "Zz9_grant_probe_" + os.urandom(12).hex()
    app_a, app_b = transfer_pair
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, b"echo-" * 1200)
        resp = ca.get(_file_path(fid), headers=_grant_headers(rid, secret))
        assert resp.status_code == 401
        assert secret not in resp.text


def test_peer_auth_get_serves_without_chunk_digests(transfer_pair):
    """Lot4+ : GET pair signé legacy sert sans morceaux (seul fetch-grant est utilisé en prod)."""
    app_a, app_b = transfer_pair
    payload = os.urandom(32 * 1024)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, sha = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            mid = db.scalar(select(HiveFileFingerprint.media_item_id).limit(1))
            fp = db.get(HiveFileFingerprint, mid)
            if fp:
                from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

                clear_chunk_digest_state(fp)
                db.commit()
        finally:
            db.close()
        pk_b = load_private_key(app_b.state.settings)
        path = _file_path(fid)
        headers = sign_request_headers(
            private_key=pk_b, method="GET", path=path, query="", ruche_id=rid, body=b""
        )
        resp = ca.get(path, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("Hive-Content-Sha256") == sha
        assert "Hive-Chunk-Sha256" not in resp.headers


def test_gossip_still_works_after_grant_lot(transfer_pair):
    app_a, app_b = transfer_pair
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        path = f"/hive/ruche/{rid.hex()}/gossip/sync"
        body = b'{"vector":{},"entries":[]}'
        pk_b = load_private_key(app_b.state.settings)
        headers = sign_request_headers(
            private_key=pk_b,
            method="POST",
            path=path,
            query="",
            ruche_id=rid,
            body=body,
        )
        headers["Content-Type"] = "application/json"
        resp = cb.post(path, headers=headers, content=body)
        assert resp.status_code == 200, resp.text
        assert "vector" in resp.json()


def test_db_stores_grant_hash_not_plaintext(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"hash-" * 800
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            row = db.scalar(select(HiveFetchGrant).limit(1))
            assert row is not None
            assert grant not in row.grant_sha256_hex
            assert row.grant_sha256_hex == hashlib.sha256(grant.encode("utf-8")).hexdigest()
        finally:
            db.close()


def _seed_file_variant(app_a, ruche_id: bytes, payload: bytes, *, tmdb_id: int) -> tuple[str, str, str]:
    settings = app_a.state.settings
    pk = load_private_key(settings)
    assert pk
    db = app_a.state.session_factory()
    try:
        from mediatracker.core.storage import storage_layout
        from mediatracker.db.models import HiveShareIntent, Media, MediaItem
        from mediatracker.hive.catalog import publish_available
        from mediatracker.library.scanner import remember_filed

        layout = storage_layout(db, settings)
        folder = layout.movies_dir / f"Fiction ({tmdb_id}) [tmdbid-{tmdb_id}]"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / f"f-{tmdb_id}.bin"
        video.write_bytes(payload)
        media = Media(type="movie", title="Fiction", year=2099, tmdb_id=tmdb_id)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloaded", downloaded=True, file_path=str(video.resolve()))
        db.add(item)
        db.flush()
        remember_filed(db, video.resolve(), media, item, NOW)
        from mediatracker.hive.chunk_digest import file_sha256_hex

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
        db.merge(HiveShareIntent(media_item_id=item.id))
        publish_available(db, settings, ruche_id=ruche_id, media=media, item=item, fingerprint_hex=sha, now=NOW)
        from tests.hive_chunk_digest_testutil import persist_chunk_digests_for_test_file

        row = db.get(HiveFileFingerprint, item.id)
        assert row is not None
        persist_chunk_digests_for_test_file(db, row)
        db.commit()
        entry = db.scalar(
            select(HiveEntry)
            .where(HiveEntry.ruche_id == ruche_id, HiveEntry.media_item_id == item.id)
            .order_by(HiveEntry.seq.desc())
            .limit(1)
        )
        assert entry and entry.file_id
        origin = hive_public_id_from_public_key(pk.public_key())
        return origin, entry.file_id.hex(), sha
    finally:
        db.close()


def test_mint_cap_limits_active_grants(transfer_pair, monkeypatch):
    monkeypatch.setattr("mediatracker.hive.fetch_grant.MAX_ACTIVE_GRANTS_PER_REQUESTER", 3)
    app_a, app_b = transfer_pair
    blob = b"cap-" * 700
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        for index in range(3):
            _, fid, _ = _seed_file_variant(app_a, rid, blob, tmdb_id=88150 + index)
            _mint(ca, app_b, rid, fid)
        _, fid_extra, _ = _seed_file_variant(app_a, rid, blob, tmdb_id=88160)
        path, headers = _sign_mint(app_b, rid, fid_extra)
        assert ca.post(path, headers=headers).status_code == 401


def test_remint_while_prior_still_valid_revokes_old(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"renew-" * 900
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        g1 = _mint(ca, app_b, rid, fid)
        g2 = _mint(ca, app_b, rid, fid)
        assert g1 != g2
        db = app_a.state.session_factory()
        try:
            active = db.scalar(
                select(func.count())
                .select_from(HiveFetchGrant)
                .where(HiveFetchGrant.revoked_at.is_(None))
            )
            assert int(active or 0) == 1
            old = db.scalar(
                select(HiveFetchGrant).where(
                    HiveFetchGrant.grant_sha256_hex == hashlib.sha256(g1.encode()).hexdigest()
                )
            )
            assert old is not None and old.revoked_at is not None
        finally:
            db.close()
        fpath = _file_path(fid)
        assert ca.get(fpath, headers=_grant_headers(rid, g1, extra={"Range": "bytes=0-15"})).status_code == 401
        assert ca.get(fpath, headers=_grant_headers(rid, g2, extra={"Range": "bytes=16-31"})).status_code in (
            200,
            206,
        )


def test_mint_replaces_grant_inside_renew_window(transfer_pair):
    from mediatracker.hive.fetch_grant import GRANT_RENEW_LEAD

    app_a, app_b = transfer_pair
    blob = b"renew2-" * 900
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        g1 = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            row = db.scalar(select(HiveFetchGrant).limit(1))
            assert row is not None
            row.expires_at = NOW + GRANT_RENEW_LEAD - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()
        g2 = _mint(ca, app_b, rid, fid)
        fpath = _file_path(fid)
        assert ca.get(fpath, headers=_grant_headers(rid, g1, extra={"Range": "bytes=0-15"})).status_code == 401
        assert ca.get(fpath, headers=_grant_headers(rid, g2, extra={"Range": "bytes=16-31"})).status_code in (
            200,
            206,
        )


def test_grant_survives_emitter_client_restart(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"restart-" * 900
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant = _mint(ca, app_b, rid, fid)
    with TestClient(app_a) as ca2:
        set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
        resp = ca2.get(_file_path(fid), headers=_grant_headers(rid, grant, extra={"Range": "bytes=0-49"}))
        assert resp.status_code in (200, 206), resp.text
        assert len(resp.content) == 50


def test_grant_respects_transfer_window_closed(transfer_pair, monkeypatch):
    app_a, app_b = transfer_pair
    closed = NOW.replace(hour=3, minute=0)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        monkeypatch.setenv("TZ", "Europe/Paris")
        monkeypatch.setattr("mediatracker.api.hive_router._now", lambda: closed)
        min_video = catalog_min_logical_video_bytes()
        blob = b"R" * min_video
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        db_a = app_a.state.session_factory()
        try:
            db_a.merge(Setting(key="hive_transfer_window_start", value="0"))
            db_a.merge(Setting(key="hive_transfer_window_end", value="1"))
            db_a.commit()
        finally:
            db_a.close()
        path = _mint_path(rid, fid)
        pk = load_private_key(app_b.state.settings)
        mint_headers = sign_request_headers(
            private_key=pk,
            method="POST",
            path=path,
            query="",
            ruche_id=rid,
            body=b"",
            when=closed,
        )
        mint_resp = ca.post(path, headers=mint_headers)
        assert mint_resp.status_code == 429, mint_resp.text


def test_grant_get_denied_when_window_closes_after_mint(transfer_pair, monkeypatch):
    from datetime import datetime, timezone

    app_a, app_b = transfer_pair
    open_at = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
    closed_at = datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        monkeypatch.setenv("TZ", "Europe/Paris")
        min_video = catalog_min_logical_video_bytes()
        blob = b"W" * min_video
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        db_a = app_a.state.session_factory()
        try:
            db_a.merge(Setting(key="hive_transfer_window_start", value="0"))
            db_a.merge(Setting(key="hive_transfer_window_end", value="1"))
            db_a.commit()
        finally:
            db_a.close()
        path = _mint_path(rid, fid)
        pk = load_private_key(app_b.state.settings)
        monkeypatch.setattr("mediatracker.api.hive_router._now", lambda: open_at)
        mint_headers = sign_request_headers(
            private_key=pk,
            method="POST",
            path=path,
            query="",
            ruche_id=rid,
            body=b"",
            when=open_at,
        )
        mint_resp = ca.post(path, headers=mint_headers)
        assert mint_resp.status_code == 200, mint_resp.text
        grant = mint_resp.json()["grant"]
        monkeypatch.setattr("mediatracker.api.hive_router._now", lambda: closed_at)
        get_resp = ca.get(
            _file_path(fid),
            headers=_grant_headers(rid, grant, extra={"Range": "bytes=0-99"}),
        )
        assert get_resp.status_code == 429, get_resp.text


def test_purge_expired_scoped_to_requester_on_mint(transfer_pair):
    app_a, app_b = transfer_pair
    blob = b"purge-" * 800
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        grant_old = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            row = db.scalar(select(HiveFetchGrant).limit(1))
            assert row is not None
            row.expires_at = NOW - timedelta(hours=1)
            db.commit()
        finally:
            db.close()
        grant_new = _mint(ca, app_b, rid, fid)
        db = app_a.state.session_factory()
        try:
            count = db.scalar(select(func.count()).select_from(HiveFetchGrant))
            assert count == 1
            row = db.scalar(select(HiveFetchGrant).limit(1))
            assert row is not None
            assert row.grant_sha256_hex == hashlib.sha256(grant_new.encode("utf-8")).hexdigest()
            assert row.grant_sha256_hex != hashlib.sha256(grant_old.encode("utf-8")).hexdigest()
        finally:
            db.close()


def test_hive_router_mint_uses_peer_auth():
    import inspect

    import mediatracker.api.hive_router as mod

    src = inspect.getsource(mod.mount_hive_router)
    assert "hive_fetch_grant_mint" in src
    chunk = src.split("hive_fetch_grant_mint")[1].split("@router.")[0]
    assert "authenticate_member_peer" in chunk
    assert "mint_fetch_grant" in chunk


def test_invalid_grant_header_blocks_peer_auth_fallback(transfer_pair):
    """Fail-closed : grant invalide => 401 même si signature pair valide (pas de repli)."""
    app_a, app_b = transfer_pair
    blob = b"fallback-" * 900
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        pk_b = load_private_key(app_b.state.settings)
        path = _file_path(fid)
        headers = sign_request_headers(
            private_key=pk_b, method="GET", path=path, query="", ruche_id=rid, body=b""
        )
        headers[HIVE_FETCH_GRANT_HEADER] = "invalid-grant-token-block-peer-failclosed"
        assert ca.get(path, headers=headers).status_code == 401
        peer_only = sign_request_headers(
            private_key=pk_b, method="GET", path=path, query="", ruche_id=rid, body=b""
        )
        assert ca.get(path, headers=peer_only).status_code in (200, 206)


def test_mint_cap_concurrent_sqlite_respects_max(transfer_pair, monkeypatch):
    monkeypatch.setattr("mediatracker.hive.fetch_grant.MAX_ACTIVE_GRANTS_PER_REQUESTER", 3)
    app_a, app_b = transfer_pair
    blob = b"conc-" * 800
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        file_ids: list[bytes] = []
        for index in range(3):
            _, fid, _ = _seed_file_variant(app_a, rid, blob, tmdb_id=88200 + index)
            file_ids.append(bytes.fromhex(fid))
        requester = cb.get("/api/v1/hive/identity").json()["instance_id"]
    outcomes: list[str] = []

    def _attempt(_n: int) -> None:
        fid = file_ids[_n % len(file_ids)]
        db = app_a.state.session_factory()
        try:
            mint_fetch_grant(
                db,
                app_a.state.settings,
                ruche_id=rid,
                file_id=fid,
                requester_id=requester,
                now=NOW,
            )
            db.commit()
            outcomes.append("ok")
        except FetchGrantDenied as exc:
            outcomes.append(exc.reason)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(_attempt, range(5)))

    assert outcomes.count("ok") == 5
    db = app_a.state.session_factory()
    try:
        active = db.scalar(
            select(func.count()).select_from(HiveFetchGrant).where(HiveFetchGrant.revoked_at.is_(None))
        )
        assert int(active or 0) == 3
    finally:
        db.close()


def test_sequential_episode_mints_revoke_after_full_serve(transfer_pair, monkeypatch):
    """33 transferts terminés n'épuisent plus le plafond de 32 grants actifs."""
    from mediatracker.hive.fetch_grant import MAX_ACTIVE_GRANTS_PER_REQUESTER

    monkeypatch.setattr(
        "mediatracker.hive.fetch_grant.MAX_ACTIVE_GRANTS_PER_REQUESTER",
        MAX_ACTIVE_GRANTS_PER_REQUESTER,
    )
    app_a, app_b = transfer_pair
    blob = b"ep-" * 512
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        for index in range(33):
            _, fid, _ = _seed_file_variant(app_a, rid, blob, tmdb_id=87000 + index)
            grant = _mint(ca, app_b, rid, fid)
            resp = ca.get(_file_path(fid), headers=_grant_headers(rid, grant))
            assert resp.status_code == 200, resp.text
            db = app_a.state.session_factory()
            try:
                row = db.scalar(
                    select(HiveFetchGrant).where(
                        HiveFetchGrant.grant_sha256_hex == hashlib.sha256(grant.encode()).hexdigest()
                    )
                )
                assert row is not None and row.revoked_at is not None
                active = db.scalar(
                    select(func.count()).select_from(HiveFetchGrant).where(HiveFetchGrant.revoked_at.is_(None))
                )
                assert int(active or 0) == 0
            finally:
                db.close()
        _, fid_extra, _ = _seed_file_variant(app_a, rid, blob, tmdb_id=87050)
        path, headers = _sign_mint(app_b, rid, fid_extra)
        assert ca.post(path, headers=headers).status_code == 200, "un 34e mint ne doit pas heurter le plafond"
