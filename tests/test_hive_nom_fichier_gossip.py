"""Hive lot A : nom de fichier d'origine dans le gossip catalogue (nom de base uniquement)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, login
from tests.test_hive_t6 import _enable, _ruche

from mediatracker.core.profile_attempt_adapters import rank_catalog_version_for_profile_attempt
from mediatracker.core.quality_profile import Profile
from mediatracker.db.models import HiveCatalogue, HiveEntry, Media, MediaItem
from mediatracker.hive.catalog import (
    _cle_document,
    _fiche_document,
    _should_skip_identical_publish,
    build_catalog_payload,
    sign_payload,
    verify_catalog_payload,
)
from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_gossip import ingest_signed_entries
from mediatracker.library.release_name import hive_gossip_basename

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(client):
    login(client)
    _enable(client)
    _ruche(client)
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def ruche_hex(client) -> str:
    login(client)
    _enable(client)
    rid = _ruche(client)
    return rid.hex()

FICTIF_RELEASE = "Fictif.Ep01.1080p.WEB-DL.MULTI.VFF.mkv"
FICTIF_PATH = f"/volume1/library/dossier/{FICTIF_RELEASE}"


def test_hive_gossip_basename_strips_path_and_rejects_dotdot():
    assert hive_gossip_basename(original_name=FICTIF_RELEASE) == FICTIF_RELEASE
    assert hive_gossip_basename(file_path=FICTIF_PATH) == FICTIF_RELEASE
    assert hive_gossip_basename(original_name="..") is None
    assert hive_gossip_basename(original_name="../secret.mkv") == "secret.mkv"
    long_name = "a" * 600 + ".mkv"
    out = hive_gossip_basename(original_name=long_name)
    assert out is not None and len(out) == 500


def test_fiche_document_includes_nom_fichier(db):
    media = Media(type="series", title="Série fictive", year=2098, tmdb_id=88001)
    item = MediaItem(
        media_id=0,
        season=1,
        episode=2,
        original_name=FICTIF_RELEASE,
        file_path=FICTIF_PATH,
    )
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    doc = _fiche_document(media, item, taille=PLAUSIBLE_VIDEO_BYTES)
    assert doc["nom_fichier"] == FICTIF_RELEASE
    assert "/" not in doc["nom_fichier"]


def test_build_catalog_payload_signatures_with_and_without_nom_fichier(client):
    _enable(client)
    pk = load_private_key(client.app.state.settings)
    assert pk is not None
    ruche_id = bytes([0xAB] * 16)
    cle = {"type": "film", "tmdb_id": 88002, "version": "1080p+VFF"}
    fiche = {
        "type": "film",
        "tmdb_id": 88002,
        "title": "Film fictif",
        "nom_fichier": "Fictif.Film.1080p.VFF.x264.mkv",
        "taille": PLAUSIBLE_VIDEO_BYTES,
    }
    payload = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id="hv1test-test-test-test-test-test-test",
        seq=1,
        cle=cle,
        fiche=fiche,
        sha256_hex="a" * 64,
        file_id=bytes([1] * 16),
        etat="disponible",
        taille=PLAUSIBLE_VIDEO_BYTES,
    )
    assert payload["nom_fichier"] == fiche["nom_fichier"]
    sig = sign_payload(pk, payload)
    verify_catalog_payload(
        client.app.state.settings,
        expected_ruche_id=ruche_id,
        payload=payload,
        signature_b64=sig,
    )
    legacy_fiche = {k: v for k, v in fiche.items() if k != "nom_fichier"}
    legacy = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id="hv1test-test-test-test-test-test-test",
        seq=2,
        cle=cle,
        fiche=legacy_fiche,
        sha256_hex="b" * 64,
        file_id=bytes([2] * 16),
        etat="disponible",
        taille=PLAUSIBLE_VIDEO_BYTES,
    )
    assert "nom_fichier" not in legacy
    sign_payload(pk, legacy)


def test_ingest_peer_keeps_unknown_fiche_field(client):

    from mediatracker.db.models import HiveEntry

    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        pk = load_private_key(client.app.state.settings)
        assert pk is not None
        origin = hive_public_id_from_public_key(pk.public_key())
        fiche = {
            "type": "film",
            "tmdb_id": 88003,
            "title": "Film fictif",
            "nom_fichier": "Fictif.CAM.1080p.VFF.mkv",
            "taille": PLAUSIBLE_VIDEO_BYTES,
        }
        payload = build_catalog_payload(
            ruche_id=rid,
            origin_instance_id=origin,
            seq=1,
            cle={"type": "film", "tmdb_id": 88003, "version": "1080p+VFF"},
            fiche=fiche,
            sha256_hex="c" * 64,
            file_id=bytes([3] * 16),
            etat="disponible",
            taille=PLAUSIBLE_VIDEO_BYTES,
        )
        sig = sign_payload(pk, payload)
        added = ingest_signed_entries(
            db, ruche_id=rid, items=[{"payload": payload, "signature_b64": sig}], now=NOW
        )
        assert added == 1
        db.commit()
        entry = db.scalar(
            select(HiveEntry).where(HiveEntry.sha256_hex == "c" * 64).order_by(HiveEntry.id.desc()).limit(1)
        )
        assert entry is not None
        stored = json.loads(entry.fiche_json or "{}")
        assert stored.get("nom_fichier") == fiche["nom_fichier"]
    finally:
        db.close()


def test_should_skip_identical_publish_false_when_nom_fichier_appears(db):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=88004)
    item = MediaItem(media_id=0, downloaded=True, original_name=FICTIF_RELEASE)
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    old_fiche = _fiche_document(media, item, taille=PLAUSIBLE_VIDEO_BYTES)
    old_fiche.pop("nom_fichier", None)
    entry = HiveEntry(
        ruche_id=bytes([9] * 16),
        origin_instance_id="hv1test-test-test-test-test-test-test",
        seq=1,
        cle_json=json.dumps(_cle_document(media, item), sort_keys=True),
        fiche_json=json.dumps(old_fiche, sort_keys=True),
        sha256_hex="d" * 64,
        file_id=bytes([4] * 16),
        etat="disponible",
        payload_json="{}",
        signature_b64="e" * 16,
        media_item_id=item.id,
    )
    assert not _should_skip_identical_publish(
        entry,
        media=media,
        item=item,
        fingerprint_hex="d" * 64,
        file_mtime_ns=None,
        taille=PLAUSIBLE_VIDEO_BYTES,
    )


def test_nom_fichier_from_sources_never_crosses_sha():
    from mediatracker.hive.source_catalog import _nom_fichier_from_sources

    sources = [
        {"sha256": "a" * 64, "nom_fichier": "Fictif.CAM.1080p.VFF.mkv"},
        {"sha256": "b" * 64, "nom_fichier": "Fictif.Clean.1080p.VFF.mkv"},
    ]
    assert _nom_fichier_from_sources(sources, "b" * 64) == "Fictif.Clean.1080p.VFF.mkv"
    assert _nom_fichier_from_sources(sources, "c" * 64) is None


def test_row_rank_not_excluded_by_other_sha_cam_name(db, ruche_hex):
    from mediatracker.hive.hive_catalogue import _stable_work_key
    from mediatracker.hive.source_catalog import rank_catalogue_row_for_profile_attempt

    profile = Profile(
        name="Profil fictif",
        languages=("VFF",),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=("CAM",),
    )
    clean_sha, cam_sha = "6" * 64, "5" * 64
    origin = "hv1test-test-test-test-test-test-test"
    _base = {
        "ruche_id_hex": ruche_hex,
        "origine": origin,
        "joignable": True,
    }
    cam_first = [
        {**_base, "file_id_hex": "01" * 16, "sha256": cam_sha, "nom_fichier": "Fictif.CAM.1080p.VFF.mkv"},
        {**_base, "file_id_hex": "02" * 16, "sha256": clean_sha, "nom_fichier": "Fictif.Clean.1080p.VFF.mkv"},
    ]
    clean_first = [
        {**_base, "file_id_hex": "03" * 16, "sha256": clean_sha, "nom_fichier": "Fictif.Clean.1080p.VFF.mkv"},
        {**_base, "file_id_hex": "04" * 16, "sha256": cam_sha, "nom_fichier": "Fictif.CAM.1080p.VFF.mkv"},
    ]
    for tmdb_id, sources in ((88010, cam_first), (88011, clean_first)):
        cle = {"type": "film", "tmdb_id": tmdb_id, "version": "1080p+VFF"}
        for sha, nom, seq in (
            (clean_sha, "Fictif.Clean.1080p.VFF.mkv", 1),
            (cam_sha, "Fictif.CAM.1080p.VFF.mkv", 2),
        ):
            fiche = {"taille": PLAUSIBLE_VIDEO_BYTES, "nom_fichier": nom}
            db.add(
                HiveEntry(
                    ruche_id=bytes.fromhex(ruche_hex),
                    origin_instance_id=origin,
                    seq=seq + tmdb_id,
                    cle_json=json.dumps(cle, sort_keys=True),
                    fiche_json=json.dumps(fiche, sort_keys=True),
                    sha256_hex=sha,
                    file_id=bytes([(seq + tmdb_id) % 256]) * 16,
                    etat="disponible",
                    payload_json=json.dumps({"fiche": fiche, "taille": PLAUSIBLE_VIDEO_BYTES}, sort_keys=True),
                )
            )
        row = HiveCatalogue(
            work_key=_stable_work_key(cle),
            version_key="1080p+VFF",
            cle_json=json.dumps(cle, sort_keys=True),
            sources_json=json.dumps(sources, sort_keys=True),
            disponible=True,
            refreshed_at=NOW,
        )
        db.add(row)
        db.commit()
        assert rank_catalogue_row_for_profile_attempt(db, row, profile=profile) is not None

    cle_cam = {"type": "film", "tmdb_id": 88012, "version": "1080p+VFF"}
    fiche_cam = {"taille": PLAUSIBLE_VIDEO_BYTES, "nom_fichier": "Fictif.CAM.1080p.VFF.mkv"}
    db.add(
        HiveEntry(
            ruche_id=bytes.fromhex(ruche_hex),
            origin_instance_id=origin,
            seq=99,
            cle_json=json.dumps(cle_cam, sort_keys=True),
            fiche_json=json.dumps(fiche_cam, sort_keys=True),
            sha256_hex=cam_sha,
            file_id=bytes([0x99]) * 16,
            etat="disponible",
            payload_json=json.dumps({"fiche": fiche_cam}, sort_keys=True),
        )
    )
    cam_only = HiveCatalogue(
        work_key=_stable_work_key(cle_cam),
        version_key="1080p+VFF",
        cle_json=json.dumps(cle_cam, sort_keys=True),
        sources_json=json.dumps(
            [{**_base, "file_id_hex": "05" * 16, "sha256": cam_sha, "nom_fichier": "Fictif.CAM.1080p.VFF.mkv"}],
            sort_keys=True,
        ),
        disponible=True,
        refreshed_at=NOW,
    )
    db.add(cam_only)
    db.commit()
    assert rank_catalogue_row_for_profile_attempt(db, cam_only, profile=profile) is None


def test_exclude_keywords_use_nom_fichier_when_provided():
    profile = Profile(
        name="Profil fictif",
        languages=("VFF",),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=("CAM",),
    )
    assert rank_catalog_version_for_profile_attempt("1080p+VFF", profile=profile) is not None
    assert (
        rank_catalog_version_for_profile_attempt(
            "1080p+VFF",
            profile=profile,
            release_text="Fictif.CAM.1080p.VFF.mkv",
        )
        is None
    )


def test_catalogue_api_exposes_nom_fichier_per_source(client, db, ruche_hex):
    login(client)
    from mediatracker.db.models import HiveCatalogue
    from mediatracker.hive.hive_catalogue import _stable_work_key

    cle = {"type": "film", "tmdb_id": 88005, "version": "1080p+VFF"}
    sources = [
        {
            "ruche_id_hex": ruche_hex,
            "origine": "hv1test-test-test-test-test-test-test",
            "file_id_hex": "aa" * 16,
            "sha256": "f" * 64,
            "joignable": True,
            "nom_fichier": "Fictif.Film.1080p.VFF.mkv",
        }
    ]
    db.add(
        HiveCatalogue(
            work_key=_stable_work_key(cle),
            version_key="1080p+VFF",
            cle_json=json.dumps(cle, sort_keys=True),
            sources_json=json.dumps(sources, sort_keys=True),
            disponible=True,
            refreshed_at=NOW,
        )
    )
    fiche = {"taille": PLAUSIBLE_VIDEO_BYTES, "nom_fichier": sources[0]["nom_fichier"]}
    db.add(
        HiveEntry(
            ruche_id=bytes.fromhex(ruche_hex),
            origin_instance_id=sources[0]["origine"],
            seq=1,
            cle_json=json.dumps(cle, sort_keys=True),
            fiche_json=json.dumps(fiche, sort_keys=True),
            sha256_hex="f" * 64,
            file_id=bytes.fromhex(sources[0]["file_id_hex"]),
            etat="disponible",
            payload_json=json.dumps({"fiche": fiche}, sort_keys=True),
        )
    )
    db.commit()
    page = client.get("/api/v1/hive/catalogue").json()
    work = next(w for w in page["items"] if w["tmdb_id"] == 88005)
    src = work["versions"][0]["sources"][0]
    assert src["nom_fichier"] == "Fictif.Film.1080p.VFF.mkv"
    assert src["sha256"] == "f" * 64


def test_merged_catalogue_carries_nom_fichier(client, db, ruche_hex):

    from mediatracker.db.models import HiveCatalogue

    cle = {"type": "film", "tmdb_id": 88006, "version": "1080p+VFF"}
    fiche = {
        "type": "film",
        "tmdb_id": 88006,
        "title": "Film fictif",
        "nom_fichier": "Fictif.Merge.1080p.VFF.mkv",
        "taille": PLAUSIBLE_VIDEO_BYTES,
    }
    db.add(
        HiveEntry(
            ruche_id=bytes.fromhex(ruche_hex),
            origin_instance_id="hv1test-test-test-test-test-test-test",
            seq=1,
            cle_json=json.dumps(cle, sort_keys=True),
            fiche_json=json.dumps(fiche, sort_keys=True),
            sha256_hex="9" * 64,
            file_id=bytes([5] * 16),
            etat="disponible",
            payload_json=json.dumps({"fiche": fiche, "taille": PLAUSIBLE_VIDEO_BYTES}, sort_keys=True),
        )
    )
    db.commit()
    refresh_hive_catalogue_store(db, now=NOW, settings=client.app.state.settings)
    db.commit()
    row = db.scalar(select(HiveCatalogue).where(HiveCatalogue.version_key == "1080p+VFF"))
    assert row is not None
    sources = json.loads(row.sources_json)
    assert sources[0].get("nom_fichier") == "Fictif.Merge.1080p.VFF.mkv"