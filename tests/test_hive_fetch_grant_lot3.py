"""LOT3 : reprise, renouvellement, intégrité et durcissement aria2 Hive."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes
from tests.test_hive_profile_attempt import _vff_principal
from tests.test_hive_t3_peer_http import _enable
from tests.test_hive_t5 import NOW, URL_A, URL_B, _join_ruche, _seed_file_on_a
from tests.test_hive_t6 import _seed_catalogue_row

from mediatracker.db.models import Download, DownloadHiveFetch, HiveMember, Media, MediaItem
from mediatracker.hive.download_aria2 import attach_or_refresh_hive_fetch, ensure_mint_for_download
from mediatracker.hive.download_pipeline import (
    hive_aria2_options,
    hive_auth_failure_message,
    hive_catalog_drift,
    scrub_hive_grant_from_session_text,
    verify_hive_completed_file,
)
from mediatracker.hive.fetch_grant import GRANT_RENEW_LEAD, HIVE_FETCH_GRANT_HEADER
from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests
from mediatracker.hive.source_catalog import pick_catalog_match_for_profile_attempt
from mediatracker.providers.downloaders.aria2 import Aria2Client

pytest_plugins = ["tests.test_hive_t5"]


def _ensure_origin_peer(db, ruche_id: bytes, origin: str, base_url: str) -> None:
    row = db.scalar(
        select(HiveMember).where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == origin).limit(1)
    )
    if row is not None:
        row.public_address = base_url
        db.flush()


def _setup_pair(app_a, app_b, ca, cb):
    _enable(ca, "aa", URL_A)
    _enable(cb, "bb", URL_B)
    rid = _join_ruche(ca, cb)
    set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
    return rid


def _pick_match(db, media, item):
    profile = _vff_principal()
    return pick_catalog_match_for_profile_attempt(
        db,
        media,
        item,
        profile=profile,
        prefer_available=True,
        skip_bitrate_sanity=True,
    )


def test_lot3_catalog_drift_blocks_refresh(transfer_pair):
    app_a, app_b = transfer_pair
    payload = os.urandom(catalog_min_logical_video_bytes())
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        origin, fid, _sha = _seed_file_on_a(app_a, rid, payload)
        db = app_b.state.session_factory()
        try:
            media = Media(type="film", title="Demo", year=2020, tmdb_id=9003, followed=True)
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, state="missing")
            db.add(item)
            db.flush()
            sha_a = "a" * 64
            _seed_catalogue_row(
                db,
                cle={"type": "film", "tmdb_id": 9003, "version": "HD+VFF"},
                version="HD+VFF",
                sha=sha_a,
                joignable=True,
                ruche_hex=rid.hex(),
                taille=len(payload),
            )
            from mediatracker.db.models import HiveCatalogue

            cat = db.scalar(select(HiveCatalogue).limit(1))
            sources = json.loads(cat.sources_json)
            sources[0]["origine"] = origin
            sources[0]["file_id_hex"] = fid
            cat.sources_json = json.dumps(sources, separators=(",", ":"))
            _ensure_origin_peer(db, rid, origin, URL_A)
            db.commit()
            match_a = _pick_match(db, media, item)
            row = Download(media_item_id=item.id, state="pending", created_at=NOW)
            db.add(row)
            db.commit()
            ctx = attach_or_refresh_hive_fetch(db, app_b.state.settings, download=row, match=match_a, now=NOW)
            assert ctx is not None
            ctx.active_source_index = 2
            db.commit()
            sha_b = "b" * 64
            match_b = replace(match_a, sha256_hex=sha_b, expected_size=len(payload) + 1)
            assert hive_catalog_drift(ctx, match_b) is not None
            refreshed = attach_or_refresh_hive_fetch(
                db, app_b.state.settings, download=row, match=match_b, now=NOW
            )
            assert refreshed is None
            db.refresh(ctx)
            assert ctx.active_source_index == 2
        finally:
            db.close()


def test_lot3_grant_renewal_before_expiry(transfer_pair, monkeypatch):
    app_a, app_b = transfer_pair
    payload = os.urandom(catalog_min_logical_video_bytes())
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        origin, fid, sha = _seed_file_on_a(app_a, rid, payload)
        db = app_b.state.session_factory()
        try:
            media = Media(type="film", title="Demo", year=2020, tmdb_id=9004, followed=True)
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, state="missing")
            db.add(item)
            db.flush()
            _seed_catalogue_row(
                db,
                cle={"type": "film", "tmdb_id": 9004, "version": "HD+VFF"},
                version="HD+VFF",
                sha=sha,
                joignable=True,
                ruche_hex=rid.hex(),
                taille=len(payload),
            )
            from mediatracker.db.models import HiveCatalogue

            cat = db.scalar(select(HiveCatalogue).limit(1))
            sources = json.loads(cat.sources_json)
            sources[0]["origine"] = origin
            sources[0]["file_id_hex"] = fid
            cat.sources_json = json.dumps(sources, separators=(",", ":"))
            row = Download(media_item_id=item.id, state="pending", created_at=NOW)
            db.add(row)
            db.commit()
            match = _pick_match(db, media, item)
            ctx = attach_or_refresh_hive_fetch(db, app_b.state.settings, download=row, match=match, now=NOW)
            assert ctx is not None
            mints = {"count": 0}
            from mediatracker.hive import download_aria2 as download_aria2_mod

            original_mint = download_aria2_mod.mint_remote_fetch_grant

            def _counting_mint(*args, **kwargs):
                mints["count"] += 1
                return original_mint(*args, **kwargs)

            monkeypatch.setattr(download_aria2_mod, "mint_remote_fetch_grant", _counting_mint)
            ensure_mint_for_download(db, app_b.state.settings, ctx, now=NOW)
            assert mints["count"] == 1
            ctx.grant_expires_at = NOW + GRANT_RENEW_LEAD - timedelta(minutes=1)
            db.commit()
            ensure_mint_for_download(db, app_b.state.settings, ctx, now=NOW)
            assert mints["count"] == 2
        finally:
            db.close()


def test_lot3_size_mismatch_keeps_file(client, tmp_path):
    payload = os.urandom(64)
    db = client.app.state.session_factory()
    try:
        media = Media(type="film", title="Demo", year=2020, tmdb_id=9005, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        sha = "c" * 64
        ruche_id = os.urandom(16)
        row = Download(media_item_id=item.id, state="post_processing", created_at=NOW)
        db.add(row)
        db.flush()
        ctx = DownloadHiveFetch(
            download_id=row.id,
            sha256_hex=sha,
            sources_json="[]",
            active_source_index=0,
            ruche_id=ruche_id,
            file_id=os.urandom(16),
            origin_instance_id="aa",
            peer_base_url=URL_A,
            expected_size=len(payload),
            content_sha256_hex=sha,
            created_at=NOW,
            updated_at=NOW,
        )
        db.add(ctx)
        db.commit()
        wrong = tmp_path / "piece.mkv"
        wrong.write_bytes(payload + b"x")
        code, message = verify_hive_completed_file(db, client.app.state.settings, row, wrong)
        assert code == "HIVE_SIZE_MISMATCH"
        assert wrong.is_file()
        assert "conservé" in message.casefold()
    finally:
        db.close()


def test_lot3_session_scrub_removes_grant_header():
    secret = "opaque-grant-value"
    text = (
        "https://peer.example/hive/files/ab\n"
        f"\theader=Hive-Ruche: {'aa' * 16}\n"
        f"\theader={HIVE_FETCH_GRANT_HEADER}: {secret}\n"
        "\tcontinue=true\n"
    )
    cleaned, removed = scrub_hive_grant_from_session_text(text)
    assert removed == 1
    assert secret not in cleaned
    assert "Hive-Ruche" in cleaned


def test_lot3_hive_aria2_options_block_redirects(client):
    db = client.app.state.session_factory()
    try:
        opts = hive_aria2_options(db)
    finally:
        db.close()
    assert opts.get("follow-redirect") == "false"
    assert opts.get("max-uri-redirection") == "0"
    assert opts.get("continue") == "true"


def test_lot3_auth_failure_detection():
    assert hive_auth_failure_message("HTTP 401 Unauthorized")
    assert hive_auth_failure_message("error 18 — Grant fetch-grant refusé")
    assert not hive_auth_failure_message("timeout")


def test_lot3_aria2_add_uri_merges_hive_options(client):
    db = client.app.state.session_factory()
    try:
        hive_opts = hive_aria2_options(db)
    finally:
        db.close()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "aria2.addUri":
            seen["options"] = body["params"][2]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "gid-h3"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "mt", "result": "ok"})

    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", "secret", transport=httpx.MockTransport(handler))
    client.add_uri(
        "https://peer.example/hive/files/" + "bb" * 16,
        Path("/tmp"),
        "piece.mkv",
        headers={"Hive-Fetch-Grant": "grant-opaque"},
        extra_options=hive_opts,
    )
    assert seen["options"]["follow-redirect"] == "false"
    client.change_option("gid-h3", {"header": ["Hive-Fetch-Grant: renewed"]})


def test_lot3_live_aria2_continue_if_binary_available(tmp_path, client):
    """Exercice aria2 réel (continue) si le binaire est installé sur la VM."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from tests.aria2_live import aria2c_path, live_aria2, wait_until

    if aria2c_path() is None:
        pytest.skip("aria2c absent de l'environnement local (aucun binaire sur la VM)")

    payload = os.urandom(8192)

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            start = 0
            raw = self.headers.get("Range")
            if raw and raw.startswith("bytes="):
                start = int(raw.split("=", 1)[1].split("-", 1)[0])
            chunk = payload[start:]
            if start > 0:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fetch_url = f"http://127.0.0.1:{server.server_address[1]}/file.bin"
    db = client.app.state.session_factory()
    try:
        hive_opts = hive_aria2_options(db)
    finally:
        db.close()
    try:
        with live_aria2(tmp_path / "aria2") as (daemon, aria):
            gid = aria.add_uri(
                fetch_url,
                daemon.incomplete_dir,
                "resume.bin",
                extra_options=hive_opts,
            )
            wait_until(aria, gid, "complete", timeout=45)
            path = daemon.incomplete_dir / "resume.bin"
            assert path.is_file()
            assert path.stat().st_size == len(payload)
    finally:
        server.shutdown()
