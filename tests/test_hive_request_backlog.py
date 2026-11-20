"""Rattrapage auto-acceptation des demandes en stock et enqueue après ingest d'offre."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login, write_sparse_video_file
from tests.test_hive_catalogue_request import _ensure_hive_enqueue_prereqs
from tests.test_hive_profile_attempt import _seed_catalogue_row
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id
from tests.test_hive_t6 import NOW, _ruche

from mediatracker.db.models import Download, HiveRequestResolution, HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import build_catalog_payload, sign_payload
from mediatracker.hive.crypto_id import generate_keypair, hive_public_id_from_public_key, public_key_to_pem
from mediatracker.hive.peer_gossip import ingest_signed_entries
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.hive.signal import build_signal_payload, cle_hash, publish_cherche_for_item
from mediatracker.hive.signal_policy import HIVE_AUTO_ACCEPT_REQUESTS_KEY
from mediatracker.hive.signal_requests import maybe_try_auto_accept_stored_incoming

pytest_plugins = ["tests.test_hive_t1"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _add_remote_cherche(db, settings, *, ruche_id: bytes, origin_remote: str, cle: dict) -> None:
    from mediatracker.hive.identity import load_private_key
    from mediatracker.hive.signal import next_signal_seq

    pk = load_private_key(settings)
    assert pk is not None
    seq = next_signal_seq(db, ruche_id, origin_remote)
    payload = build_signal_payload(
        ruche_id=ruche_id, origin_instance_id=origin_remote, seq=seq, signal="cherche", cle=cle
    )
    db.add(
        HiveSignal(
            ruche_id=ruche_id,
            origin_instance_id=origin_remote,
            seq=seq,
            created_at=NOW,
            signal="cherche",
            cle_hash=cle_hash(cle),
            cle_json=json.dumps(cle),
            payload_json=json.dumps(payload),
            signature_b64=sign_payload(pk, payload),
        )
    )
    db.flush()


def test_auto_accept_stored_incoming_when_setting_enabled(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        remote = "peerBacklogPlateau880060"
        cle = {"type": "movie", "tmdb_id": 880060, "version": "1080p+fr"}
        _add_remote_cherche(db, settings, ruche_id=rid, origin_remote=remote, cle=cle)
        media = Media(type="movie", title="Œuvre locale", year=2098, tmdb_id=880060, followed=False)
        db.add(media)
        db.flush()
        fpath = tmp_path / "local.mkv"
        write_sparse_video_file(fpath, 128_000)
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item)
        db.flush()
        db.merge(Setting(key=HIVE_AUTO_ACCEPT_REQUESTS_KEY, value="true", is_secret=False, updated_at=NOW))
        maybe_try_auto_accept_stored_incoming(db, settings, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveRequestResolution).where(
                HiveRequestResolution.ruche_id == rid,
                HiveRequestResolution.requester_origin == remote,
                HiveRequestResolution.status == "accepted",
            )
        )
        assert row is not None
        db.commit()
    finally:
        db.close()


def test_auto_accept_stored_incoming_after_share_grant(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        remote = "peerBacklogPlateau880061"
        cle = {"type": "movie", "tmdb_id": 880061, "version": "1080p+fr"}
        _add_remote_cherche(db, settings, ruche_id=rid, origin_remote=remote, cle=cle)
        media = Media(type="movie", title="Œuvre locale B", year=2098, tmdb_id=880061, followed=False)
        db.add(media)
        db.flush()
        fpath = tmp_path / "localB.mkv"
        write_sparse_video_file(fpath, 128_000)
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item)
        db.flush()
        db.merge(Setting(key=HIVE_AUTO_ACCEPT_REQUESTS_KEY, value="true", is_secret=False, updated_at=NOW))
        grant_share_intent(db, item.id)
        maybe_try_auto_accept_stored_incoming(db, settings, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveRequestResolution).where(
                HiveRequestResolution.ruche_id == rid,
                HiveRequestResolution.requester_origin == remote,
            )
        )
        assert row is not None and row.status == "accepted"
        db.commit()
    finally:
        db.close()


def test_auto_accept_via_settings_patch(client):
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        remote = "peerBacklogPlateau880062"
        cle = {"type": "movie", "tmdb_id": 880062, "version": "1080p+fr"}
        _add_remote_cherche(db, settings, ruche_id=rid, origin_remote=remote, cle=cle)
        from pathlib import Path

        media = Media(type="movie", title="Œuvre locale C", year=2098, tmdb_id=880062, followed=False)
        db.add(media)
        db.flush()
        lib = Path(settings.data_dir) / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        fpath = lib / "localC.mkv"
        write_sparse_video_file(fpath, 128_000)
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item)
        db.flush()
        grant_share_intent(db, item.id)
        db.commit()
    finally:
        db.close()
    resp = client.patch(
        "/api/v1/settings",
        json={"values": {HIVE_AUTO_ACCEPT_REQUESTS_KEY: True}},
        headers=csrf(client),
    )
    assert resp.status_code == 200, resp.text
    db = client.app.state.session_factory()
    try:
        row = db.scalar(
            select(HiveRequestResolution).where(HiveRequestResolution.requester_origin == remote)
        )
        assert row is not None and row.status == "accepted"
    finally:
        db.close()


def test_try_enqueue_for_ingested_offer_with_active_cherche(client, settings):
    _enable_hive(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        _ensure_hive_enqueue_prereqs(db, rid.hex())
        cle = {"type": "film", "tmdb_id": 880070, "version": "1080p+VFF"}
        media = Media(
            type="film",
            title="Film demandeur",
            year=2099,
            tmdb_id=880070,
            followed=True,
            pref_quality="1080p",
            pref_language="VFF",
            new_episode_policy="auto",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing", release_quality="1080p", release_language="VFF")
        db.add(item)
        db.flush()
        _seed_catalogue_row(
            db,
            cle=cle,
            version="1080p+VFF",
            sha="d" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
        )
        publish_cherche_for_item(db, settings, media=media, item=item, now=NOW)
        from mediatracker.hive.source_download import try_enqueue_hive_for_ingested_offers

        queued_n = try_enqueue_hive_for_ingested_offers(db, settings, ruche_id=rid, cles=[cle], now=NOW)
        assert queued_n == 1
        dl_count = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item.id))
        assert dl_count == 1
        queued_repeat = try_enqueue_hive_for_ingested_offers(db, settings, ruche_id=rid, cles=[cle], now=NOW)
        assert queued_repeat == 0
        db.commit()
    finally:
        db.close()


def test_ingest_disponible_triggers_enqueue_hook(client, settings, monkeypatch):
    _enable_hive(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    calls: list[int] = []

    def _spy(*_a, **kwargs):
        calls.append(len(kwargs.get("cles") or []))
        return 0

    monkeypatch.setattr(
        "mediatracker.hive.source_download.try_enqueue_hive_for_ingested_offers",
        _spy,
    )
    try:
        peer_sk, peer_pub = generate_keypair()
        peer_origin = hive_public_id_from_public_key(peer_pub)
        from mediatracker.db.models import HiveMember

        db.add(
            HiveMember(
                ruche_id=rid,
                member_instance_id=peer_origin,
                public_address="https://pair-offre.exemple.test",
                public_key_pem=public_key_to_pem(peer_pub).decode("ascii"),
                joined_at=NOW,
            )
        )
        db.flush()
        cle = {"type": "film", "tmdb_id": 880071, "version": "1080p+VFF"}
        sha = "e" * 64
        size = 50_000_000
        fiche = {"type": "film", "tmdb_id": 880071, "title": "Film offre", "year": 2099, "taille": size}
        payload = build_catalog_payload(
            ruche_id=rid,
            origin_instance_id=peer_origin,
            seq=1,
            cle=cle,
            fiche=fiche,
            sha256_hex=sha,
            file_id=b"\x03" * 16,
            etat="disponible",
            taille=size,
        )
        sig = sign_payload(peer_sk, payload)
        accepted = ingest_signed_entries(
            db,
            ruche_id=rid,
            items=[{"payload": payload, "signature_b64": sig}],
            now=NOW,
            settings=settings,
        )
        assert accepted == 1
        assert calls == [1]
        db.commit()
    finally:
        db.close()


def test_auto_accept_stored_incoming_skipped_when_setting_off(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        remote = "peerBacklogPlateau880063"
        cle = {"type": "movie", "tmdb_id": 880063, "version": "1080p+fr"}
        _add_remote_cherche(db, settings, ruche_id=rid, origin_remote=remote, cle=cle)
        media = Media(type="movie", title="Œuvre locale D", year=2098, tmdb_id=880063, followed=False)
        db.add(media)
        db.flush()
        fpath = tmp_path / "localD.mkv"
        write_sparse_video_file(fpath, 128_000)
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item)
        db.flush()
        grant_share_intent(db, item.id)
        maybe_try_auto_accept_stored_incoming(db, settings, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveRequestResolution).where(HiveRequestResolution.requester_origin == remote)
        )
        assert row is None
        auto_share = db.scalar(select(Setting).where(Setting.key == "hive_catalog_auto_share"))
        assert auto_share is None or str(auto_share.value).lower() in {"", "false", "0"}
        db.commit()
    finally:
        db.close()


def test_ingest_disponible_enqueues_download_when_cherche_and_reachable(client, settings):
    _enable_hive(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        _ensure_hive_enqueue_prereqs(db, rid.hex())
        cle = {"type": "film", "tmdb_id": 880072, "version": "1080p+VFF"}
        media = Media(
            type="film",
            title="Film ingest réel",
            year=2099,
            tmdb_id=880072,
            followed=True,
            pref_quality="1080p",
            pref_language="VFF",
            new_episode_policy="auto",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing", release_quality="1080p", release_language="VFF")
        db.add(item)
        db.flush()
        publish_cherche_for_item(db, settings, media=media, item=item, now=NOW)
        peer_sk, peer_pub = generate_keypair()
        peer_origin = hive_public_id_from_public_key(peer_pub)
        from mediatracker.db.models import HiveMember
        from mediatracker.hive.peer_gossip import record_reachability

        db.add(
            HiveMember(
                ruche_id=rid,
                member_instance_id=peer_origin,
                public_address="https://pair-offre-reelle.exemple.test",
                public_key_pem=public_key_to_pem(peer_pub).decode("ascii"),
                joined_at=NOW,
            )
        )
        db.flush()
        record_reachability(db, ruche_id=rid, origin_id=peer_origin, now=NOW)
        sha = "f" * 64
        size = PLAUSIBLE_VIDEO_BYTES
        fiche = {"type": "film", "tmdb_id": 880072, "title": "Film ingest réel", "year": 2099, "taille": size}
        payload = build_catalog_payload(
            ruche_id=rid,
            origin_instance_id=peer_origin,
            seq=1,
            cle=cle,
            fiche=fiche,
            sha256_hex=sha,
            file_id=b"\x04" * 16,
            etat="disponible",
            taille=size,
        )
        sig = sign_payload(peer_sk, payload)
        accepted = ingest_signed_entries(
            db,
            ruche_id=rid,
            items=[{"payload": payload, "signature_b64": sig}],
            now=NOW,
            settings=settings,
        )
        assert accepted == 1
        dl_count = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item.id))
        assert dl_count == 1
        accepted_again = ingest_signed_entries(
            db,
            ruche_id=rid,
            items=[{"payload": payload, "signature_b64": sig}],
            now=NOW,
            settings=settings,
        )
        assert accepted_again == 0
        dl_count_again = db.scalar(select(func.count()).select_from(Download).where(Download.media_item_id == item.id))
        assert dl_count_again == 1
        db.commit()
    finally:
        db.close()


def test_ingest_defers_enqueue_when_cles_are_collected(client, settings, monkeypatch):
    _enable_hive(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    refresh_calls: list[int] = []
    enqueue_calls: list[int] = []

    def _refresh(*_a, **_k):
        refresh_calls.append(1)
        return 0

    def _enqueue(*_a, **kwargs):
        enqueue_calls.append(len(kwargs.get("cles") or []))
        return 0

    monkeypatch.setattr("mediatracker.hive.hive_catalogue.refresh_hive_catalogue_store", _refresh)
    monkeypatch.setattr(
        "mediatracker.hive.source_download.try_enqueue_hive_for_ingested_offers",
        _enqueue,
    )
    try:
        peer_sk, peer_pub = generate_keypair()
        peer_origin = hive_public_id_from_public_key(peer_pub)
        from mediatracker.db.models import HiveMember

        db.add(
            HiveMember(
                ruche_id=rid,
                member_instance_id=peer_origin,
                public_address="https://pair-pages.exemple.test",
                public_key_pem=public_key_to_pem(peer_pub).decode("ascii"),
                joined_at=NOW,
            )
        )
        db.flush()
        collected: list[dict] = []
        for seq in (1, 2):
            cle = {"type": "film", "tmdb_id": 880073 + seq, "version": "1080p+VFF"}
            sha = ("a" if seq == 1 else "b") * 64
            size = 50_000_000
            fiche = {"type": "film", "tmdb_id": cle["tmdb_id"], "title": "Film page", "year": 2099, "taille": size}
            payload = build_catalog_payload(
                ruche_id=rid,
                origin_instance_id=peer_origin,
                seq=seq,
                cle=cle,
                fiche=fiche,
                sha256_hex=sha,
                file_id=bytes([seq]) * 16,
                etat="disponible",
                taille=size,
            )
            sig = sign_payload(peer_sk, payload)
            accepted = ingest_signed_entries(
                db,
                ruche_id=rid,
                items=[{"payload": payload, "signature_b64": sig}],
                now=NOW,
                settings=settings,
                collected_disponible_cles=collected,
            )
            assert accepted == 1
        assert len(collected) == 2
        assert refresh_calls == []
        assert enqueue_calls == []
        db.commit()
    finally:
        db.close()
