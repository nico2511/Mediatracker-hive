"""T8 : repartage automatique après réception Hive (sans republication manuelle)."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login
from tests.test_hive_t3_peer_http import _enable as peer_enable
from tests.test_hive_t5 import NOW, URL_A, URL_B, _join_ruche, _seed_file_on_a
from tests.test_hive_t6 import NOW as T6_NOW
from tests.test_hive_t6 import _enable, _ingest, _ruche

from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveCatalogue,
    HiveEntry,
    HiveFileFingerprint,
    HiveTransferJob,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.hive.catalog import item_is_shareable_base
from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store
from mediatracker.hive.hive_received_filing import finalize_received_hive_file
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_gossip import (
    REACHABILITY_TTL_SECONDS,
    mark_origin_unreachable,
    origin_is_reachable,
    record_reachability,
)
from mediatracker.hive.share_policy import HIVE_CATALOG_AUTO_SHARE_KEY
from mediatracker.hive.source_catalog import pick_catalog_match
from mediatracker.hive.source_poll import run_hive_follow_poll

pytest_plugins = ["tests.test_hive_t5"]


def test_received_file_auto_published_in_second_ruche(transfer_pair, tmp_path):
    """X reçoit de Y dans A ; après rangement, entrée signée automatique dans B."""
    app_y, app_x = transfer_pair
    with TestClient(app_y) as cy, TestClient(app_x) as cx:
        peer_enable(cy, "aa", URL_A)
        peer_enable(cx, "bb", URL_B)
        rid_a = _join_ruche(cy, cx, invitee_url=URL_B)
        login(cx)
        rid_b = cx.post("/api/v1/hive/ruches", headers=csrf(cx)).json()["ruche_id_hex"]
        payload = b"REPART-T8-" + b"x" * 512
        origin_y, _fid_y, sha = _seed_file_on_a(app_y, rid_a, payload)
        part = tmp_path / "received.mkv"
        part.write_bytes(payload)
        db_x = app_x.state.session_factory()
        try:
            fiche = {"type": "movie", "tmdb_id": 99902, "title": "Repart", "year": 2099}
            cle = {"type": "movie", "tmdb_id": 99902, "version": "u+u"}
            media = Media(type="film", title="Repart", year=2099, tmdb_id=99902, followed=True)
            db_x.add(media)
            db_x.flush()
            item = MediaItem(media_id=media.id, state="missing")
            db_x.add(item)
            db_x.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="true"))
            db_x.commit()
            err = finalize_received_hive_file(
                db_x,
                app_x.state.settings,
                part=part,
                media=media,
                item=item,
                fiche=fiche,
                cle=cle,
                sha256_hex=sha,
                now=NOW,
            )
            assert err is None
            db_x.commit()
            origin_x = hive_public_id_from_public_key(load_private_key(app_x.state.settings).public_key())
            from mediatracker.hive.catalog import member_ruche_ids_for_instance

            assert bytes.fromhex(rid_b) in member_ruche_ids_for_instance(db_x, origin_x)
            fp = db_x.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.sha256_hex == sha))
            assert fp is not None
            item = db_x.get(MediaItem, fp.media_item_id)
            assert item is not None
            assert item_is_shareable_base(db_x, item, hive_enabled=True)
            row_b = db_x.scalar(
                select(HiveEntry).where(
                    HiveEntry.ruche_id == bytes.fromhex(rid_b),
                    HiveEntry.origin_instance_id == origin_x,
                    HiveEntry.etat == "disponible",
                )
            )
            assert row_b is not None
            assert row_b.sha256_hex == sha
            signed = json.loads(row_b.payload_json or "{}")
            blob = json.dumps(signed)
            assert origin_y not in blob
            assert rid_a.hex() not in blob
        finally:
            db_x.close()


def test_reachability_ttl_controlled_clock(client):
    _enable(client)
    db = client.app.state.session_factory()
    rid = b"\x99" * 16
    origin = "hv1peer-peer-peer-peer-peer-peer-peer"
    t0 = T6_NOW
    try:
        from mediatracker.db.models import HiveRuche

        db.add(HiveRuche(ruche_id=rid, creator_instance_id=origin))
        db.flush()
        record_reachability(db, ruche_id=rid, origin_id=origin, now=t0)
        db.commit()
        assert origin_is_reachable(
            db, ruche_id=rid, origin_id=origin, now=t0 + timedelta(seconds=REACHABILITY_TTL_SECONDS - 1)
        )
        assert not origin_is_reachable(
            db, ruche_id=rid, origin_id=origin, now=t0 + timedelta(seconds=REACHABILITY_TTL_SECONDS + 1)
        )
    finally:
        db.close()


def test_version_stays_available_when_one_origin_down(client):
    """X injoignable mais W même version encore joignable → catalogue et choix source OK."""
    _enable(client)
    db = client.app.state.session_factory()
    rid = _ruche(client)
    origin_bad = "hv1aaa-aaa-aaa-aaa-aaa-aaa-aaa-aaa"
    origin_good = "hv1bbb-bbb-bbb-bbb-bbb-bbb-bbb-bbb"
    sha = "c" * 64
    cle_json = json.dumps(
        {"type": "film", "tmdb_id": 99911, "version": "HD+VFF"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        fiche = {
            "type": "film",
            "tmdb_id": 99911,
            "title": "Multi",
            "year": 2099,
            "taille": PLAUSIBLE_VIDEO_BYTES,
        }
        payload = {"taille": PLAUSIBLE_VIDEO_BYTES, "fiche": fiche}
        fiche_json = json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for seq, origin, fid in ((1, origin_bad, b"\x11" * 16), (2, origin_good, b"\x22" * 16)):
            db.add(
                HiveEntry(
                    ruche_id=rid,
                    origin_instance_id=origin,
                    seq=seq,
                    cle_json=cle_json,
                    fiche_json=fiche_json,
                    sha256_hex=sha,
                    file_id=fid,
                    etat="disponible",
                    payload_json=payload_json,
                )
            )
        record_reachability(db, ruche_id=rid, origin_id=origin_good, now=NOW)
        mark_origin_unreachable(db, ruche_id=rid, origin_id=origin_bad, now=NOW)
        db.commit()
        assert refresh_hive_catalogue_store(db, now=NOW) >= 1
        db.commit()
        row = db.scalar(select(HiveCatalogue).where(HiveCatalogue.version_key == "HD+VFF").limit(1))
        assert row is not None and row.disponible is True
        sources = json.loads(row.sources_json or "[]")
        assert any(s.get("joignable") for s in sources)
        assert any(s.get("origine") == origin_good and s.get("joignable") for s in sources)
        assert any(s.get("origine") == origin_bad and not s.get("joignable") for s in sources)
        media = Media(
            type="film",
            title="Multi",
            year=2099,
            tmdb_id=99911,
            followed=True,
            new_episode_policy="auto",
            pref_quality="HD",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.commit()
        match = pick_catalog_match(
            db,
            media,
            item,
            allow_fallback=True,
            quality_fallback=[],
            language_fallback=[],
            profile=None,
            prefer_available=True,
        )
        assert match is not None and match.expected_size == PLAUSIBLE_VIDEO_BYTES
        joignable = [s for s in match.sources if s.get("joignable")]
        assert joignable and joignable[0]["origine"] == origin_good
    finally:
        db.close()


def test_announce_without_taille_recovers_after_valid_republish(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    try:
        media = Media(
            type="film",
            title="Sans taille puis OK",
            year=2099,
            tmdb_id=8811,
            followed=True,
            new_episode_policy="auto",
            catch_up_pending=True,
            pref_quality="1080p",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.commit()
        sha_old = "1" * 64
        _ingest(
            db,
            settings,
            ruche_id=rid,
            tmdb_id=8811,
            season=None,
            episode=None,
            version="1080p+VFF",
            media_type="film",
            sha=sha_old,
            joignable=True,
            seq=1,
            size=0,
        )
        refresh_hive_catalogue_store(db, now=T6_NOW)
        db.commit()
        report = run_hive_follow_poll(db, media, settings, force=True, now=T6_NOW)
        assert report is not None
        assert report.queued == 0
        assert db.scalar(select(HiveTransferJob).limit(1)) is None
        _ingest(
            db,
            settings,
            ruche_id=rid,
            tmdb_id=8811,
            season=None,
            episode=None,
            version="1080p+VFF",
            media_type="film",
            sha=sha_old,
            joignable=True,
            seq=2,
            size=PLAUSIBLE_VIDEO_BYTES,
        )
        refresh_hive_catalogue_store(db, now=T6_NOW)
        db.commit()
        match = pick_catalog_match(
            db,
            media,
            item,
            allow_fallback=True,
            quality_fallback=[],
            language_fallback=[],
            profile=None,
            prefer_available=True,
        )
        assert match is not None
        assert match.expected_size == PLAUSIBLE_VIDEO_BYTES
        report2 = run_hive_follow_poll(db, media, settings, force=True, now=T6_NOW)
        assert report2 is not None
        assert report2.queued == 1
        dl = db.scalar(select(Download).limit(1))
        assert dl is not None
        ctx = db.get(DownloadHiveFetch, dl.id)
        assert ctx is not None
        assert int(ctx.expected_size) == PLAUSIBLE_VIDEO_BYTES
    finally:
        db.close()
