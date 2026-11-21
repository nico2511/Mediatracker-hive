"""Hive V1a : signaux possede/cherche, demandes anonymes, gossip."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from tests.conftest import TEST_ADMIN_PASSWORD, login
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.config import Settings
from mediatracker.db.models import HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import sign_payload
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_gossip import ingest_signed_entries
from mediatracker.hive.signal import (
    build_signal_payload,
    cle_hash,
    ingest_signed_signals,
    publish_cherche_for_item,
    sync_possede_for_item,
)
from mediatracker.hive.signal_miss_prompt import cherche_miss_prompt_active
from mediatracker.hive.signal_policy import (
    HIVE_REQUEST_ON_MISS_KEY,
    alldebrid_configured,
    hive_request_on_miss_mode,
    maybe_publish_cherche_on_follow,
    maybe_publish_cherche_on_source_miss,
    should_publish_cherche_on_follow,
    should_publish_cherche_on_miss,
)
from mediatracker.integrations.alldebrid_key import effective_alldebrid_key


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def test_legacy_catalog_ingest_ignores_signal_document(client, settings):
    """Compat pair ancien : ingest catalogue refuse un document signal."""
    _enable_hive(client)
    pk = load_private_key(settings)
    assert pk is not None
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        cle = {"type": "movie", "tmdb_id": 880001, "version": "1080p+fr"}
        payload = build_signal_payload(
            ruche_id=rid,
            origin_instance_id=origin,
            seq=1,
            signal="cherche",
            cle=cle,
        )
        sig = sign_payload(pk, payload)
        n = ingest_signed_entries(db, ruche_id=rid, items=[{"payload": payload, "signature_b64": sig}], now=_utc_now())
        db.commit()
        assert n == 0
        assert db.scalar(select(HiveSignal).limit(1)) is None
    finally:
        db.close()


def test_publish_cherche_without_alldebrid(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        assert not alldebrid_configured(settings, db)
        assert should_publish_cherche_on_follow(settings, db)
        media = Media(type="movie", title="Œuvre plateau A", year=2099, tmdb_id=880002, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None)
        db.add(item)
        db.flush()
        assert publish_cherche_for_item(db, settings, media=media, item=item, now=_utc_now())
        db.flush()
        row = db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1))
        assert row is not None
        payload = json.loads(row.payload_json or "{}")
        assert payload.get("kind") == "signal"
        added = ingest_signed_signals(
            db,
            ruche_id=row.ruche_id,
            items=[{"payload": payload, "signature_b64": row.signature_b64}],
            now=_utc_now(),
        )
        assert added == 0
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["auto", "ask", "never"])
def test_request_on_miss_with_alldebrid_key(client, settings, mode):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    db = client.app.state.session_factory()
    try:
        assert effective_alldebrid_key(settings, db) == "fictive-adb-tst-k"
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value=mode))
        db.flush()
        assert hive_request_on_miss_mode(db) == mode
        assert should_publish_cherche_on_follow(settings, db) is False
        assert should_publish_cherche_on_miss(settings, db) == (mode == "auto")
        db.commit()
    finally:
        db.close()


def test_alldebrid_auto_publishes_only_after_source_miss(client, settings):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value="auto"))
        media = Media(type="movie", title="Œuvre plateau C", year=2099, tmdb_id=880003, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id)
        db.add(item)
        db.flush()
        moment = _utc_now()
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=moment)
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=moment)
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is not None
        db.commit()
    finally:
        db.close()


def test_possede_retire_when_file_gone_without_touching_cherche(client, settings, tmp_path):
    from mediatracker.hive.catalog import _cle_document
    from mediatracker.hive.signal import _latest_signal_row

    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        moment = _utc_now()
        media_p = Media(type="movie", title="Œuvre plateau D", year=2098, tmdb_id=880004, followed=False)
        db.add(media_p)
        db.flush()
        fpath = tmp_path / "owned.mkv"
        fpath.write_bytes(b"\x02" * 64)
        item_p = MediaItem(
            media_id=media_p.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item_p)
        media_c = Media(type="movie", title="Œuvre plateau D2", year=2098, tmdb_id=880014, followed=True)
        db.add(media_c)
        db.flush()
        item_c = MediaItem(media_id=media_c.id)
        db.add(item_c)
        db.flush()
        cle_p = _cle_document(media_p, item_p)
        sync_possede_for_item(db, settings, media=media_p, item=item_p, now=moment)
        publish_cherche_for_item(db, settings, media=media_c, item=item_c, now=moment)
        db.flush()
        ch_row = db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1))
        assert ch_row is not None
        fpath.unlink()
        sync_possede_for_item(db, settings, media=media_p, item=item_p, now=moment)
        db.flush()
        latest_p = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle_p)
        assert latest_p is not None and latest_p.signal == "retire"
        ch_after = db.get(HiveSignal, ch_row.id)
        assert ch_after is not None and ch_after.signal == "cherche"
        db.commit()
    finally:
        db.close()


def test_ask_mode_sets_prompt_not_signal_until_confirm(client, settings):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value="ask"))
        media = Media(type="movie", title="Œuvre plateau E", year=2097, tmdb_id=880005, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id)
        db.add(item)
        db.flush()
        moment = _utc_now()
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=moment)
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        assert item.id is not None
        assert cherche_miss_prompt_active(db, item.id)
        from mediatracker.hive.signal_policy import explicit_announce_cherche

        assert explicit_announce_cherche(db, settings, media=media, item=item, now=moment)
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is not None
        assert not cherche_miss_prompt_active(db, item.id)
        db.commit()
    finally:
        db.close()


def test_signal_compaction_keeps_single_row_per_origin_cle(client, settings):
    from sqlalchemy import func

    from mediatracker.hive.signal import _compact_signal_rows, next_signal_seq

    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        rid = _join_fixture_ruche(db, _origin_id(settings))
        origin = _origin_id(settings)
        pk = load_private_key(settings)
        assert pk is not None
        cle = {"type": "movie", "tmdb_id": 880006, "version": "1080p+fr"}
        h = cle_hash(cle)
        moment = _utc_now()
        for sig in ("possede", "cherche", "retire"):
            seq = next_signal_seq(db, rid, origin)
            payload = build_signal_payload(
                ruche_id=rid, origin_instance_id=origin, seq=seq, signal=sig, cle=cle
            )
            db.add(
                HiveSignal(
                    ruche_id=rid,
                    origin_instance_id=origin,
                    seq=seq,
                    created_at=moment,
                    signal=sig,
                    cle_hash=h,
                    cle_json=json.dumps(cle),
                    payload_json=json.dumps(payload),
                    signature_b64=sign_payload(pk, payload),
                )
            )
            db.flush()
            row = db.scalar(select(HiveSignal).order_by(HiveSignal.id.desc()).limit(1))
            assert row is not None
            _compact_signal_rows(db, ruche_id=rid, origin_id=origin, cle_key=h, keep_id=row.id)
        count = db.scalar(
            select(func.count())
            .select_from(HiveSignal)
            .where(
                HiveSignal.ruche_id == rid,
                HiveSignal.origin_instance_id == origin,
                HiveSignal.cle_hash == h,
            )
        )
        assert count == 1
        db.commit()
    finally:
        db.close()


def test_received_api_never_leaks_origin(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        pk = load_private_key(settings)
        assert pk is not None
        origin_remote = "remotePeerPlateau880099"
        cle = {"type": "movie", "tmdb_id": 880011, "version": "1080p+fr"}
        from mediatracker.hive.signal import next_signal_seq

        seq = next_signal_seq(db, rid, origin_remote)
        payload = build_signal_payload(
            ruche_id=rid, origin_instance_id=origin_remote, seq=seq, signal="cherche", cle=cle
        )
        db.add(
            HiveSignal(
                ruche_id=rid,
                origin_instance_id=origin_remote,
                seq=seq,
                created_at=_utc_now(),
                signal="cherche",
                cle_hash=cle_hash(cle),
                cle_json=json.dumps(cle),
                payload_json=json.dumps(payload),
                signature_b64=sign_payload(pk, payload),
            )
        )
        media_match = Media(type="film", title="Fichier local B", year=2098, tmdb_id=880011, followed=False)
        db.add(media_match)
        db.flush()
        lib = tmp_path / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        fpath = lib / "local.mkv"
        fpath.write_bytes(b"\x01" * 128)
        local_item = MediaItem(
            media_id=media_match.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(local_item)
        db.flush()
        from mediatracker.hive.signal_requests import list_received_requests

        rows = list_received_requests(db, settings)
        blob = json.dumps(rows)
        assert "origine" not in blob.lower()
        assert origin_remote not in blob
        assert len(rows) == 1
        db.commit()
    finally:
        db.close()

    login(client)
    resp = client.get("/api/v1/hive/requests/received")
    assert resp.status_code == 200
    assert origin_remote not in resp.text