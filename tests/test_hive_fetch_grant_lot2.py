"""LOT2 : Download Hive + mint grant + aria2 (fetch-grant toujours actif)."""

from __future__ import annotations

import json
import os

from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes
from tests.test_hive_profile_attempt import _vff_principal
from tests.test_hive_t3_peer_http import _enable
from tests.test_hive_t5 import NOW, URL_A, URL_B, _join_ruche, _seed_file_on_a
from tests.test_hive_t6 import _seed_catalogue_row

from mediatracker.db.models import Download, HiveMember, HiveTransferJob, Media, MediaItem
from mediatracker.hive.download_aria2 import (
    aria2_headers_for_ctx,
    attach_or_refresh_hive_fetch,
    ensure_mint_for_download,
    hive_file_download_url,
)
from mediatracker.hive.fetch_grant import HIVE_FETCH_GRANT_HEADER
from mediatracker.hive.peer_auth import HIVE_RUCHE_HEADER
from mediatracker.hive.peer_transport import peer_request, set_peer_asgi_apps_for_tests
from mediatracker.hive.source_catalog import pick_catalog_match_for_profile_attempt
from mediatracker.hive.source_poll import enqueue_hive_match

pytest_plugins = ["tests.test_hive_t5"]


def _setup_pair(app_a, app_b, ca, cb):
    _enable(ca, "aa", URL_A)
    _enable(cb, "bb", URL_B)
    rid = _join_ruche(ca, cb)
    set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
    return rid


def _ensure_origin_peer(db, ruche_id: bytes, origin: str, base_url: str) -> None:
    row = db.scalar(
        select(HiveMember).where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == origin).limit(1)
    )
    if row is not None:
        row.public_address = base_url
        db.flush()


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


def test_lot2_enqueue_creates_download_not_transfer_job(transfer_pair):
    app_a, app_b = transfer_pair
    payload = os.urandom(catalog_min_logical_video_bytes())
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        origin, fid, sha = _seed_file_on_a(app_a, rid, payload)
        db = app_b.state.session_factory()
        try:
            cle = {"type": "film", "tmdb_id": 9001, "version": "HD+VFF"}
            media = Media(
                type="film",
                title="Demo",
                year=2020,
                tmdb_id=9001,
                followed=True,
                new_episode_policy="auto",
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
            assert cat is not None
            sources = json.loads(cat.sources_json)
            sources[0]["origine"] = origin
            sources[0]["file_id_hex"] = fid
            cat.sources_json = json.dumps(sources, separators=(",", ":"))
            _ensure_origin_peer(db, rid, origin, URL_A)
            db.commit()
            match = _pick_match(db, media, item)
            assert match is not None
            created = enqueue_hive_match(db, app_b.state.settings, media, item, match, now=NOW)
            assert isinstance(created, Download)
            assert db.scalar(select(HiveTransferJob).limit(1)) is None
        finally:
            db.close()


def test_lot2_prepare_unlock_and_aria2_style_get(transfer_pair):
    """Mint grant, URL aria2, GET pair avec en-têtes (sans digests)."""
    app_a, app_b = transfer_pair
    payload = os.urandom(catalog_min_logical_video_bytes())
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        origin, fid, sha = _seed_file_on_a(app_a, rid, payload)
        db = app_b.state.session_factory()
        try:
            from mediatracker.core.quality_profile import ensure_default_profile

            ensure_default_profile(db)
            media = Media(
                type="film",
                title="Demo",
                year=2020,
                tmdb_id=9001,
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
                cle={"type": "film", "tmdb_id": 9001, "version": "HD+VFF"},
                version="HD+VFF",
                sha=sha,
                joignable=True,
                ruche_hex=rid.hex(),
                taille=len(payload),
            )
            from mediatracker.db.models import HiveCatalogue

            cat = db.scalar(select(HiveCatalogue).limit(1))
            assert cat is not None
            sources = json.loads(cat.sources_json)
            sources[0]["origine"] = origin
            sources[0]["file_id_hex"] = fid
            cat.sources_json = json.dumps(sources, separators=(",", ":"))
            _ensure_origin_peer(db, rid, origin, URL_A)
            row = Download(
                media_item_id=item.id,
                state="pending",
                created_at=NOW,
            )
            db.add(row)
            db.commit()
            match = _pick_match(db, media, item)
            assert match is not None
            ctx = attach_or_refresh_hive_fetch(
                db, app_b.state.settings, download=row, match=match, now=NOW
            )
            assert ctx is not None
            grant = ensure_mint_for_download(db, app_b.state.settings, ctx, now=NOW)
            url = hive_file_download_url(ctx.peer_base_url, ctx.file_id)
            headers = aria2_headers_for_ctx(ctx, grant.grant_secret)
            size = int(ctx.expected_size)
            assert size == len(payload)
            assert HIVE_RUCHE_HEADER in headers
            assert HIVE_FETCH_GRANT_HEADER in headers
            assert url.endswith(f"/hive/files/{fid}")
            path = f"/hive/files/{fid}"
            resp = peer_request(
                base_url=URL_A,
                method="GET",
                url_path=path,
                headers=headers,
                body=b"",
            )
            assert resp.status_code == 200, resp.text
            assert len(resp.content) == len(payload)
            assert resp.content == payload
        finally:
            db.close()
