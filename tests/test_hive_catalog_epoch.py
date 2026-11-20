"""Époque de catalogue signée : reset créateur, purge et anti-réinjection."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from tests.conftest import csrf, login
from tests.test_hive_t2 import NOW, _enable_hive, _origin_id
from tests.test_hive_t3 import _create_ruche_api

from mediatracker.db.models import HiveEntry, HiveMember, HiveMemberList, HiveRuche, HiveSignal, Media, MediaItem
from mediatracker.hive.catalog_epoch import payload_catalog_epoch, ruche_confirm_name
from mediatracker.hive.member_list import (
    apply_member_list_as_peer,
    build_member_list_payload,
    current_members_as_records,
    latest_member_list_version,
    sign_member_list,
)
from mediatracker.hive.membership import local_instance_identity, reset_ruche_catalog
from mediatracker.hive.peer_gossip import ingest_signed_entries
from mediatracker.hive.peer_info import KNOWN_HIVE_CAPABILITIES, build_local_instance_snapshot
from mediatracker.hive.signal import build_signal_payload, ingest_signed_signals


def _sign_catalog(db, settings, *, ruche_id: bytes, seq: int, epoch: int) -> tuple[dict, str]:
    pk, creator_id, *_ = local_instance_identity(db, settings)
    assert pk is not None
    payload = {
        "v": 1,
        "ruche_id": ruche_id.hex(),
        "origine": creator_id,
        "seq": seq,
        "catalog_epoch": epoch,
        "cle": {"type": "movie", "tmdb_id": 900001, "version": "hd+fr"},
        "fiche": {"type": "movie", "tmdb_id": 900001, "title": "Fixture", "year": 2000},
        "sha256": "a" * 64,
        "file_id": ("b" * 32),
        "etat": "disponible",
    }
    from mediatracker.hive.catalog import sign_payload

    return payload, sign_payload(pk, payload)


def test_catalog_reset_api_rejects_bad_name(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    login(client)
    bad = client.post(
        f"/api/v1/hive/ruches/{rid}/catalog-reset",
        json={"confirm_name": "wrong"},
        headers=csrf(client),
    )
    assert bad.status_code == 400


def test_reset_increments_epoch_and_resigns_member_list(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    ruche_id = bytes.fromhex(rid)
    login(client)
    ok = client.post(
        f"/api/v1/hive/ruches/{rid}/catalog-reset",
        json={"confirm_name": ruche_confirm_name(ruche_id)},
        headers=csrf(client),
    )
    assert ok.status_code == 204
    db.expire_all()
    ruche = db.get(HiveRuche, ruche_id)
    assert ruche is not None
    assert int(ruche.catalog_epoch) == 1
    assert ruche.catalog_reset_at is not None
    ver = latest_member_list_version(db, ruche_id)
    assert ver == 2
    row = db.scalar(select(HiveMemberList).where(HiveMemberList.ruche_id == ruche_id, HiveMemberList.version == ver))
    assert row is not None
    payload = json.loads(row.payload_json)
    assert payload_catalog_epoch(payload) == 1


def test_apply_higher_epoch_purges_catalog_and_signals_keeps_members_and_media(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    pk, creator_id, doc, sig_b64, pem, _ = local_instance_identity(db, settings)
    ruche_id = bytes.fromhex(rid)
    media = Media(type="movie", tmdb_id=900002, title="Fixture work", year=2010)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None)
    db.add(item)
    db.add(
        HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=creator_id,
            seq=1,
            cle_json="{}",
            etat="disponible",
            media_item_id=item.id,
        )
    )
    db.add(
        HiveSignal(
            ruche_id=ruche_id,
            origin_instance_id=creator_id,
            seq=1,
            created_at=NOW,
            signal="cherche",
            cle_hash="deadbeef",
            cle_json="{}",
        )
    )
    db.commit()
    members = current_members_as_records(db, ruche_id)
    version = latest_member_list_version(db, ruche_id) + 1
    reset_at = NOW.isoformat().replace("+00:00", "Z")
    payload = build_member_list_payload(
        ruche_id=ruche_id,
        version=version,
        members=members,
        catalog_epoch=1,
        catalog_reset_at=reset_at,
    )
    sig = sign_member_list(pk, payload)
    apply_member_list_as_peer(
        db,
        ruche_id=ruche_id,
        creator_id=creator_id,
        creator_public_key=pk.public_key(),
        payload=payload,
        signature_b64=sig,
        now=NOW,
        settings=settings,
    )
    db.commit()
    assert db.scalar(select(func.count()).select_from(HiveEntry).where(HiveEntry.ruche_id == ruche_id)) == 0
    assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.ruche_id == ruche_id)) == 0
    assert db.scalar(select(func.count()).select_from(HiveMember).where(HiveMember.ruche_id == ruche_id)) == 1
    assert db.get(Media, media.id) is not None
    assert db.get(MediaItem, item.id) is not None


def test_stale_epoch_gossip_ignored(client, app, settings):
    _enable_hive(client)
    rid_hex = _create_ruche_api(client)
    db = app.state.session_factory()
    rid = bytes.fromhex(rid_hex)
    origin = _origin_id(settings)
    ruche = db.get(HiveRuche, rid)
    assert ruche is not None
    ruche.catalog_epoch = 2
    db.commit()
    payload, sig = _sign_catalog(db, settings, ruche_id=rid, seq=1, epoch=1)
    added = ingest_signed_entries(db, ruche_id=rid, items=[{"payload": payload, "signature_b64": sig}], now=NOW)
    assert added == 0
    sig_payload = build_signal_payload(
        ruche_id=rid,
        origin_instance_id=origin,
        seq=1,
        signal="cherche",
        cle={"type": "movie", "tmdb_id": 900003, "version": "hd+fr"},
        catalog_epoch=1,
    )
    from mediatracker.hive.catalog import sign_payload

    pk = __import__("mediatracker.hive.identity", fromlist=["load_private_key"]).load_private_key(settings)
    assert pk is not None
    sig_b64 = sign_payload(pk, sig_payload)
    sig_added = ingest_signed_signals(
        db, ruche_id=rid, items=[{"payload": sig_payload, "signature_b64": sig_b64}], now=NOW
    )
    assert sig_added == 0


def test_legacy_member_list_without_epoch_fields(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    pk, creator_id, doc, sig_b64, pem, _ = local_instance_identity(db, settings)
    ruche_id = bytes.fromhex(rid)
    members = current_members_as_records(db, ruche_id)
    payload = {
        "v": 1,
        "ruche_id": ruche_id.hex(),
        "version": latest_member_list_version(db, ruche_id) + 1,
        "members": members,
    }
    sig = sign_member_list(pk, payload)
    row = apply_member_list_as_peer(
        db,
        ruche_id=ruche_id,
        creator_id=creator_id,
        creator_public_key=pk.public_key(),
        payload=payload,
        signature_b64=sig,
        now=NOW,
        settings=settings,
    )
    assert row is not None


def test_peer_info_advertises_catalog_epoch_capability():
    snap = build_local_instance_snapshot(datetime.now(timezone.utc))
    assert "catalog_epoch" in snap["capabilities"]
    assert "catalog_epoch" in KNOWN_HIVE_CAPABILITIES


def test_reset_ruche_catalog_unit(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    ruche_id = bytes.fromhex(rid)
    with pytest.raises(ValueError, match="confirm_name_mismatch"):
        reset_ruche_catalog(db, settings, ruche_id=ruche_id, confirm_name="nope", now=NOW)
    ruche = db.get(HiveRuche, ruche_id)
    assert ruche is not None
    ruche.creator_instance_id = "not-the-local-creator"
    db.flush()
    with pytest.raises(ValueError, match="not_creator"):
        reset_ruche_catalog(
            db, settings, ruche_id=ruche_id, confirm_name=ruche_confirm_name(ruche_id), now=NOW
        )
    ruche.creator_instance_id = local_instance_identity(db, settings)[1]
    reset_ruche_catalog(db, settings, ruche_id=ruche_id, confirm_name=ruche_confirm_name(ruche_id), now=NOW)
    db.commit()
    assert int(db.get(HiveRuche, ruche_id).catalog_epoch or 0) == 1
