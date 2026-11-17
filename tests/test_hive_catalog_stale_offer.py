"""Catalogue Hive : tombstones, offres obsolètes, resolve_local_file, version conseillée."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES
from tests.test_hive_t6 import _enable, _ruche

from mediatracker.db.models import (
    HiveEntry,
    HiveFileFingerprint,
    HiveMember,
    HiveRuche,
    LibraryFile,
    Media,
    MediaItem,
)
from mediatracker.hive.catalog import build_catalog_payload, publish_available, sign_payload, sync_hive_catalog_item
from mediatracker.hive.crypto_id import public_key_to_pem
from mediatracker.hive.file_serve import resolve_local_file
from mediatracker.hive.hive_catalogue import build_merged_hive_catalogue, refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_gossip import ingest_signed_entries
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.hive.source_catalog import pick_catalog_match_for_profile_attempt

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(client):
    _enable(client)
    _ruche(client)
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def ruche_hex(client) -> str:
    _enable(client)
    rid = _ruche(client)
    return rid.hex()
SUSPECT_BYTES = 2600
GOOD_BYTES = PLAUSIBLE_VIDEO_BYTES
ON_DISK_BYTES = 65536


def _join_ruche(db, origin: str, ruche_id: bytes, settings) -> None:
    pk = load_private_key(settings)
    pem = public_key_to_pem(pk.public_key()).decode("utf-8") if pk else ""
    if db.get(HiveRuche, ruche_id) is None:
        db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=origin))
    db.add(
        HiveMember(
            ruche_id=ruche_id,
            member_instance_id=origin,
            public_address="https://mtr.exemple.fr",
            public_key_pem=pem,
        )
    )


def _seed_video_item(
    db, settings, *, tmdb_id: int, size: int, sha: str, path: Path
) -> tuple[Media, MediaItem, bytes, str]:
    origin = hive_public_id_from_public_key(load_private_key(settings).public_key())
    ruche_id = bytes([0xAB] * 15 + [1])
    _join_ruche(db, origin, ruche_id, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    on_disk = min(int(size), ON_DISK_BYTES)
    path.write_bytes(b"x" * on_disk)
    media = Media(type="film", title="Fictif", year=2099, tmdb_id=tmdb_id, pref_quality="HD", pref_language="VFF")
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        downloaded=True,
        file_path=str(path),
        release_quality="HD",
        release_language="VFF",
    )
    db.add(item)
    db.flush()
    st = path.stat()
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    db.add(
        HiveFileFingerprint(
            media_item_id=item.id,
            path=str(path.resolve()),
            size=on_disk,
            mtime_ns=mtime_ns,
            sha256_hex=sha,
            partial_size=on_disk,
            updated_at=NOW,
        )
    )
    db.add(
        LibraryFile(
            path=str(path),
            size=on_disk,
            parsed_title="Fictif",
            parsed_year=2099,
            media_type="film",
            tmdb_id=tmdb_id,
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
    )
    grant_share_intent(db, item.id)
    db.commit()
    return media, item, ruche_id, origin


def test_merge_latest_tombstone_hides_disponible(db, settings):
    ruche_id = bytes([0xCD] * 15 + [2])
    origin = "origin-test-01"
    cle = {"type": "film", "tmdb_id": 88001, "version": "unknown+unknown"}
    cle_json = '{"episode":null,"season":null,"tmdb_id":88001,"type":"film","version":"unknown+unknown"}'
    db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=origin))
    db.add(
        HiveMember(
            ruche_id=ruche_id,
            member_instance_id=origin,
            public_address="https://mtr.exemple.fr",
            public_key_pem="",
        )
    )
    db.add(
        HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=3,
            cle_json=cle_json,
            fiche_json='{"taille":2600,"title":"Fictif","tmdb_id":88001,"type":"film"}',
            sha256_hex="a" * 64,
            file_id=bytes.fromhex("11" * 16),
            etat="disponible",
            payload_json='{"taille":2600,"etat":"disponible","sha256":"%s"}' % ("a" * 64),
            signature_b64="dGVzdA==",
        )
    )
    db.add(
        HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=4,
            cle_json=cle_json,
            fiche_json='{"taille":2600}',
            sha256_hex="a" * 64,
            file_id=bytes.fromhex("11" * 16),
            etat="retire",
            payload_json='{"etat":"retire"}',
            signature_b64="dGVzdA==",
            tombstone_at=NOW,
        )
    )
    db.commit()
    merged = build_merged_hive_catalogue(db, now=NOW)
    versions = [v for w in merged for v in w.get("versions", []) if v.get("version") == cle["version"]]
    assert versions == []


def test_gossip_rejects_tiny_unknown_disponible(db, settings):
    pk = load_private_key(settings)
    assert pk is not None
    origin = hive_public_id_from_public_key(pk.public_key())
    ruche_id = bytes([0xEF] * 15 + [3])
    _join_ruche(db, origin, ruche_id, settings)
    payload = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=1,
        cle={"type": "film", "tmdb_id": 88002, "version": "unknown+unknown"},
        fiche={"type": "film", "tmdb_id": 88002, "title": "Fictif", "taille": SUSPECT_BYTES},
        sha256_hex="b" * 64,
        file_id=bytes.fromhex("22" * 16),
        etat="disponible",
        taille=SUSPECT_BYTES,
    )
    sig = sign_payload(pk, payload)
    accepted = ingest_signed_entries(
        db, ruche_id=ruche_id, items=[{"payload": payload, "signature_b64": sig}], now=NOW
    )
    assert accepted == 0


def test_resolve_local_file_none_when_entry_sha_mismatches_fingerprint(client, app, settings, tmp_path):
    from tests.conftest import csrf, login

    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://mtr.exemple.fr"}},
        headers=csrf(client),
    )
    media, item, ruche_id, origin = _seed_video_item(
        app.state.session_factory(),
        settings,
        tmdb_id=88003,
        size=GOOD_BYTES,
        sha="c" * 64,
        path=tmp_path / "resolve.mkv",
    )
    db = app.state.session_factory()
    try:
        publish_available(db, settings, ruche_id=ruche_id, media=media, item=item, fingerprint_hex="c" * 64, now=NOW)
        entry = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item.id).order_by(HiveEntry.seq.desc()))
        assert entry is not None
        entry.sha256_hex = "d" * 64
        db.commit()
        binding = resolve_local_file(
            db,
            settings,
            ruche_id=ruche_id,
            file_id=entry.file_id,
        )
        assert binding is None
    finally:
        db.close()


def test_publish_tombstones_stale_fingerprint_on_disk(client, app, settings, tmp_path):
    from tests.conftest import csrf, login

    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://mtr.exemple.fr"}},
        headers=csrf(client),
    )
    path = tmp_path / "stale-sync.mkv"
    media, item, ruche_id, _origin = _seed_video_item(
        app.state.session_factory(),
        settings,
        tmdb_id=88004,
        size=ON_DISK_BYTES,
        sha="e" * 64,
        path=path,
    )
    db = app.state.session_factory()
    try:
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        fp.size = SUSPECT_BYTES
        db.flush()
        sync_hive_catalog_item(db, settings, item.id, now=NOW)
        db.commit()
        last = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item.id).order_by(HiveEntry.seq.desc()))
        assert last is not None
        assert last.etat == "retire"
    finally:
        db.close()


def test_recommended_version_ignores_tiny_unknown(db, settings, ruche_hex):
    from tests.test_hive_profile_attempt import _seed_catalogue_row, _vff_principal

    media = Media(type="film", title="Fictif", year=2099, tmdb_id=88005, pref_quality="HD", pref_language="VFF")
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None, state="missing")
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 88005, "version": "unknown+unknown"},
        version="unknown+unknown",
        sha="f" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=SUSPECT_BYTES,
        file_id_hex="f0" * 16,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 88005, "version": "HD+VFF"},
        version="HD+VFF",
        sha="1" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=GOOD_BYTES,
        file_id_hex="10" * 16,
    )
    refresh_hive_catalogue_store(db, now=NOW)
    db.commit()
    principal = _vff_principal()
    match = pick_catalog_match_for_profile_attempt(db, media, item, profile=principal)
    assert match is not None
    assert match.row.version_key == "HD+VFF"


def test_stale_unknown_not_in_refreshed_catalogue(db, settings):
    origin = "origin-merge-88006"
    ruche_id = bytes([0x12] * 15 + [4])
    stale_cle = '{"tmdb_id":88006,"type":"film","version":"unknown+unknown"}'
    good_cle = '{"tmdb_id":88006,"type":"film","version":"HD+VFF"}'
    db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=origin))
    db.add(
        HiveMember(
            ruche_id=ruche_id,
            member_instance_id=origin,
            public_address="https://mtr.exemple.fr",
            public_key_pem="",
        )
    )
    db.add_all(
        [
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=1,
                cle_json=stale_cle,
                fiche_json=f'{{"taille":{SUSPECT_BYTES}}}',
                sha256_hex="3" * 64,
                file_id=bytes.fromhex("44" * 16),
                etat="disponible",
                payload_json=f'{{"taille":{SUSPECT_BYTES}}}',
                signature_b64="dGVzdA==",
            ),
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=2,
                cle_json=stale_cle,
                fiche_json=f'{{"taille":{SUSPECT_BYTES}}}',
                sha256_hex="3" * 64,
                file_id=bytes.fromhex("44" * 16),
                etat="retire",
                payload_json='{"etat":"retire"}',
                signature_b64="dGVzdA==",
                tombstone_at=NOW,
            ),
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=1,
                cle_json=good_cle,
                fiche_json=f'{{"taille":{GOOD_BYTES}}}',
                sha256_hex="2" * 64,
                file_id=bytes.fromhex("33" * 16),
                etat="disponible",
                payload_json=f'{{"taille":{GOOD_BYTES}}}',
                signature_b64="dGVzdA==",
            ),
        ]
    )
    db.commit()
    refresh_hive_catalogue_store(db, now=NOW)
    merged = build_merged_hive_catalogue(db, now=NOW)
    keys = {v.get("version") for w in merged for v in w.get("versions", [])}
    assert "unknown+unknown" not in keys
    assert "HD+VFF" in keys


def _plausible_disponible_payload(
    *,
    ruche_id: bytes,
    origin: str,
    seq: int,
    file_id: bytes,
    sha256_hex: str,
    tmdb_id: int = 88100,
):
    return build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=seq,
        cle={"type": "film", "tmdb_id": tmdb_id, "version": "HD+VFF"},
        fiche={"type": "film", "tmdb_id": tmdb_id, "title": "Fictif", "taille": GOOD_BYTES},
        sha256_hex=sha256_hex,
        file_id=file_id,
        etat="disponible",
        taille=GOOD_BYTES,
    )


def _ingest_payload(db, ruche_id: bytes, pk, payload: dict) -> int:
    sig = sign_payload(pk, payload)
    return ingest_signed_entries(
        db, ruche_id=ruche_id, items=[{"payload": payload, "signature_b64": sig}], now=NOW
    )


def test_mask_lifted_on_republished_disponible_newer_seq(db, settings, ruche_hex):
    from mediatracker.hive.source_offer_mask import is_source_offer_masked, mask_source_offer, masked_source_keys

    pk = load_private_key(settings)
    assert pk is not None
    origin = hive_public_id_from_public_key(pk.public_key())
    ruche_id = bytes.fromhex(ruche_hex)
    file_id = bytes.fromhex("66" * 16)
    old_sha = "a" * 64
    new_sha = "b" * 64
    assert _ingest_payload(db, ruche_id, pk, _plausible_disponible_payload(
        ruche_id=ruche_id, origin=origin, seq=10, file_id=file_id, sha256_hex=old_sha
    )) == 1
    mask_source_offer(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=old_sha,
        reason="source_404",
        now=NOW,
    )
    db.commit()
    assert is_source_offer_masked(db, ruche_id=ruche_id, origin_instance_id=origin, file_id=file_id)
    assert (ruche_id, origin, file_id) in masked_source_keys(db)
    assert _ingest_payload(
        db,
        ruche_id,
        pk,
        _plausible_disponible_payload(
            ruche_id=ruche_id, origin=origin, seq=11, file_id=file_id, sha256_hex=new_sha
        ),
    ) == 1
    db.commit()
    assert not is_source_offer_masked(db, ruche_id=ruche_id, origin_instance_id=origin, file_id=file_id)
    refresh_hive_catalogue_store(db, now=NOW)
    merged = build_merged_hive_catalogue(db, now=NOW)
    assert any(
        src.get("sha256") == new_sha
        for work in merged
        for ver in work.get("versions", [])
        for src in ver.get("sources", [])
    )


def test_mask_lifted_on_republished_disponible_new_sha(db, settings, ruche_hex):
    from mediatracker.hive.source_offer_mask import (
        is_source_offer_masked,
        mask_source_offer,
        maybe_lift_source_offer_mask,
    )

    ruche_id = bytes.fromhex(ruche_hex)
    origin = "origin-mask-lift-by-sha"
    file_id = bytes.fromhex("77" * 16)
    old_sha = "c" * 64
    new_sha = "d" * 64
    db.add(
        HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=10,
            sha256_hex=old_sha,
            file_id=file_id,
            etat="disponible",
            cle_json='{"type":"film","tmdb_id":88101,"version":"HD+VFF"}',
            signature_b64="dGVzdA==",
        )
    )
    db.flush()
    mask_source_offer(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=old_sha,
        reason="source_404",
        now=NOW,
    )
    assert not maybe_lift_source_offer_mask(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=old_sha,
        seq=10,
    )
    assert maybe_lift_source_offer_mask(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=new_sha,
        seq=10,
    )
    assert not is_source_offer_masked(db, ruche_id=ruche_id, origin_instance_id=origin, file_id=file_id)


def test_mask_not_lifted_without_newer_seq_or_sha(db, settings, ruche_hex):
    from mediatracker.hive.source_offer_mask import (
        is_source_offer_masked,
        mask_source_offer,
        maybe_lift_source_offer_mask,
    )

    origin = "origin-mask-88102"
    ruche_id = bytes.fromhex(ruche_hex)
    file_id = bytes.fromhex("88" * 16)
    sha = "e" * 64
    db.add(HiveEntry(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=12,
        sha256_hex=sha,
        file_id=file_id,
        etat="disponible",
        cle_json='{"type":"film","tmdb_id":88102,"version":"HD+VFF"}',
        signature_b64="dGVzdA==",
    ))
    db.flush()
    mask_source_offer(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=sha,
        reason="source_404",
        now=NOW,
    )
    assert not maybe_lift_source_offer_mask(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=sha,
        seq=12,
    )
    assert not maybe_lift_source_offer_mask(
        db,
        ruche_id=ruche_id,
        origin_instance_id=origin,
        file_id=file_id,
        sha256_hex=sha,
        seq=8,
    )
    assert is_source_offer_masked(db, ruche_id=ruche_id, origin_instance_id=origin, file_id=file_id)
