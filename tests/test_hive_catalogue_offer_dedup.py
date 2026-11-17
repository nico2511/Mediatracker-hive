"""Dédoublonnage des offres catalogue par file_id (même cle, plusieurs fichiers locaux)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, login

from mediatracker.db.models import HiveEntry, HiveMember, HiveRuche
from mediatracker.hive.hive_catalogue import _stable_work_key, build_merged_hive_catalogue, refresh_hive_catalogue_store

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
GOOD_BYTES = PLAUSIBLE_VIDEO_BYTES


@pytest.fixture
def db(client):
    from tests.test_hive_t6 import _enable, _ruche

    _enable(client)
    _ruche(client)
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _join_origin(db, *, ruche_id: bytes, origin: str) -> None:
    db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=origin))
    db.add(
        HiveMember(
            ruche_id=ruche_id,
            member_instance_id=origin,
            public_address="https://mtr.exemple.fr",
            public_key_pem="",
        )
    )


def _disponible_entry(
    *,
    ruche_id: bytes,
    origin: str,
    seq: int,
    cle: dict,
    file_id: bytes | None,
    sha: str,
    nom_fichier: str | None = None,
) -> HiveEntry:
    cle_json = json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fiche = {
        "type": cle["type"],
        "tmdb_id": cle["tmdb_id"],
        "title": "Fictif",
        "taille": GOOD_BYTES,
    }
    if nom_fichier:
        fiche["nom_fichier"] = nom_fichier
    payload = {"taille": GOOD_BYTES, "etat": "disponible", "sha256": sha}
    return HiveEntry(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=seq,
        cle_json=cle_json,
        fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        sha256_hex=sha,
        file_id=file_id,
        etat="disponible",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        signature_b64="dGVzdA==",
    )


def test_merge_two_files_same_cle_same_origin_two_sources(db):
    ruche_id = bytes([0xA1] * 15 + [9])
    origin = "origin-dedup-two-files"
    cle = {"type": "film", "tmdb_id": 88201, "version": "HD+VFF"}
    sha_a = "a" * 64
    sha_b = "b" * 64
    _join_origin(db, ruche_id=ruche_id, origin=origin)
    db.add_all(
        [
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=1,
                cle=cle,
                file_id=bytes.fromhex("aa" * 16),
                sha=sha_a,
            ),
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=2,
                cle=cle,
                file_id=bytes.fromhex("bb" * 16),
                sha=sha_b,
            ),
        ]
    )
    db.commit()
    merged = build_merged_hive_catalogue(db, now=NOW)
    versions = [v for w in merged for v in w.get("versions", []) if v.get("version") == "HD+VFF"]
    assert len(versions) == 1
    sources = versions[0]["sources"]
    assert len(sources) == 2
    shas = {s.get("sha256") for s in sources}
    assert shas == {sha_a, sha_b}


def test_merge_tombstone_one_file_id_leaves_other(db):
    ruche_id = bytes([0xA2] * 15 + [9])
    origin = "origin-dedup-tombstone-partial"
    cle = {"type": "film", "tmdb_id": 88202, "version": "HD+VFF"}
    cle_json = json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    file_keep = bytes.fromhex("cc" * 16)
    file_gone = bytes.fromhex("dd" * 16)
    sha_keep = "c" * 64
    sha_gone = "d" * 64
    fiche = json.dumps(
        {"type": "film", "tmdb_id": 88202, "title": "Fictif", "taille": GOOD_BYTES},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _join_origin(db, ruche_id=ruche_id, origin=origin)
    db.add_all(
        [
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=1,
                cle=cle,
                file_id=file_keep,
                sha=sha_keep,
            ),
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=1,
                cle=cle,
                file_id=file_gone,
                sha=sha_gone,
            ),
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=5,
                cle_json=cle_json,
                fiche_json=fiche,
                sha256_hex=sha_gone,
                file_id=file_gone,
                etat="retire",
                payload_json='{"etat":"retire"}',
                signature_b64="dGVzdA==",
                tombstone_at=NOW,
            ),
        ]
    )
    db.commit()
    merged = build_merged_hive_catalogue(db, now=NOW)
    sources = [
        src
        for w in merged
        for v in w.get("versions", [])
        for src in v.get("sources", [])
        if w.get("cle", {}).get("tmdb_id") == 88202
    ]
    assert len(sources) == 1
    assert sources[0].get("sha256") == sha_keep
    assert sources[0].get("file_id_hex") == file_keep.hex()


def test_merge_same_file_id_reclassified_version_keeps_latest_seq_only(db):
    """Même fichier physique : une reclassification ne laisse que la dernière annonce."""
    ruche_id = bytes([0xA4] * 15 + [9])
    origin = "origin-reclass-file-id"
    file_id = bytes.fromhex("ef" * 16)
    sha_old = "0" * 64
    sha_new = "1" * 64
    cle_old = {"type": "film", "tmdb_id": 88205, "version": "HD+VFF"}
    cle_new = {"type": "film", "tmdb_id": 88205, "version": "1080p+VOSTFR"}
    _join_origin(db, ruche_id=ruche_id, origin=origin)
    db.add_all(
        [
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=1,
                cle=cle_old,
                file_id=file_id,
                sha=sha_old,
            ),
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=2,
                cle=cle_new,
                file_id=file_id,
                sha=sha_new,
            ),
        ]
    )
    db.commit()
    merged = build_merged_hive_catalogue(db, now=NOW)
    by_version = {
        v.get("version"): v
        for w in merged
        for v in w.get("versions", [])
        if w.get("cle", {}).get("tmdb_id") == 88205
    }
    assert "HD+VFF" not in by_version
    assert "1080p+VOSTFR" in by_version
    sources = by_version["1080p+VOSTFR"]["sources"]
    assert len(sources) == 1
    assert sources[0].get("sha256") == sha_new
    assert sources[0].get("file_id_hex") == file_id.hex()


def test_merge_legacy_without_file_id_still_dedup_by_cle(db):
    """Paire ancienne sans file_id : le dédoublonnage reste sur cle_json."""
    ruche_id = bytes([0xA3] * 15 + [9])
    origin = "origin-dedup-legacy-cle"
    cle = {"type": "film", "tmdb_id": 88203, "version": "HD+VFF"}
    cle_json = json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fiche = json.dumps(
        {"type": "film", "tmdb_id": 88203, "title": "Fictif", "taille": GOOD_BYTES},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    _join_origin(db, ruche_id=ruche_id, origin=origin)
    db.add_all(
        [
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=1,
                cle_json=cle_json,
                fiche_json=fiche,
                sha256_hex="1" * 64,
                file_id=None,
                etat="disponible",
                payload_json=json.dumps({"taille": GOOD_BYTES, "etat": "disponible"}),
                signature_b64="dGVzdA==",
            ),
            HiveEntry(
                ruche_id=ruche_id,
                origin_instance_id=origin,
                seq=2,
                cle_json=cle_json,
                fiche_json=fiche,
                sha256_hex="2" * 64,
                file_id=None,
                etat="disponible",
                payload_json=json.dumps({"taille": GOOD_BYTES, "etat": "disponible"}),
                signature_b64="dGVzdA==",
            ),
        ]
    )
    db.commit()
    merged = build_merged_hive_catalogue(db, now=NOW)
    sources = [
        src
        for w in merged
        for v in w.get("versions", [])
        for src in v.get("sources", [])
        if w.get("cle", {}).get("tmdb_id") == 88203
    ]
    assert len(sources) == 1
    assert sources[0].get("sha256") == "2" * 64


def test_merged_catalogue_exposes_nom_fichier_per_source(db):
    ruche_id = bytes([0xA4] * 15 + [9])
    origin = "origin-dedup-nom-fichier"
    cle = {"type": "film", "tmdb_id": 88204, "version": "HD+VFF"}
    _join_origin(db, ruche_id=ruche_id, origin=origin)
    db.add_all(
        [
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=1,
                cle=cle,
                file_id=bytes.fromhex("e1" * 16),
                sha="e" * 64,
                nom_fichier="copie-a.mkv",
            ),
            _disponible_entry(
                ruche_id=ruche_id,
                origin=origin,
                seq=2,
                cle=cle,
                file_id=bytes.fromhex("e2" * 16),
                sha="f" * 64,
                nom_fichier="copie-b.mkv",
            ),
        ]
    )
    db.commit()
    merged = build_merged_hive_catalogue(db, now=NOW)
    sources = [
        src
        for w in merged
        for v in w.get("versions", [])
        for src in v.get("sources", [])
        if w.get("cle", {}).get("tmdb_id") == 88204
    ]
    assert {s.get("nom_fichier") for s in sources} == {"copie-a.mkv", "copie-b.mkv"}
    assert len({s.get("sha256") for s in sources}) == 2


def test_api_catalogue_work_two_versions_three_sources(client):
    """GET catalogue + fiche : 2 versions, 3 sources (VFF×2 + VO×1) avec sha et nom_fichier."""
    from tests.test_hive_t6 import _enable

    login(client)
    _enable(client)
    tmdb_id = 88210
    ruche_id = bytes([0xB1] * 15 + [1])
    origin = "origin-api-multi-version"
    cle_vff = {"type": "film", "tmdb_id": tmdb_id, "version": "HD+VFF"}
    cle_vo = {"type": "film", "tmdb_id": tmdb_id, "version": "HD+VO"}
    work_key = _stable_work_key(cle_vff)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    try:
        _join_origin(db, ruche_id=ruche_id, origin=origin)
        db.add_all(
            [
                _disponible_entry(
                    ruche_id=ruche_id,
                    origin=origin,
                    seq=1,
                    cle=cle_vff,
                    file_id=bytes.fromhex("f1" * 16),
                    sha="1" * 64,
                    nom_fichier="release-vff-a.mkv",
                ),
                _disponible_entry(
                    ruche_id=ruche_id,
                    origin=origin,
                    seq=2,
                    cle=cle_vff,
                    file_id=bytes.fromhex("f2" * 16),
                    sha="2" * 64,
                    nom_fichier="release-vff-b.mkv",
                ),
                _disponible_entry(
                    ruche_id=ruche_id,
                    origin=origin,
                    seq=1,
                    cle=cle_vo,
                    file_id=bytes.fromhex("f3" * 16),
                    sha="3" * 64,
                    nom_fichier="release-vo.mkv",
                ),
            ]
        )
        refresh_hive_catalogue_store(db, now=NOW, settings=settings)
        db.commit()
    finally:
        db.close()

    assert client.get("/api/v1/hive/overview").json()["enabled"] is True
    page = client.get("/api/v1/hive/catalogue?refresh=1").json()
    work = next(w for w in page["items"] if w.get("work_key") == work_key)
    assert len(work["versions"]) == 2
    assert sum(v["source_count"] for v in work["versions"]) == 3

    by_vk = {v["version_key"]: v for v in work["versions"]}
    vff = by_vk["HD+VFF"]
    vo = by_vk["HD+VO"]
    assert vff["source_count"] == 2
    assert vo["source_count"] == 1
    assert len(vff["sources"]) == 2
    assert len(vo["sources"]) == 1
    assert {s["sha256"] for s in vff["sources"]} == {"1" * 64, "2" * 64}
    assert {s["nom_fichier"] for s in vff["sources"]} == {"release-vff-a.mkv", "release-vff-b.mkv"}
    assert vo["sources"][0]["sha256"] == "3" * 64
    assert vo["sources"][0]["nom_fichier"] == "release-vo.mkv"

    detail = client.get(f"/api/v1/hive/catalogue/{work_key}").json()
    assert detail["work_key"] == work_key
    assert len(detail["versions"]) == 2
    assert sum(v["source_count"] for v in detail["versions"]) == 3
    detail_by_vk = {v["version_key"]: v for v in detail["versions"]}
    assert len(detail_by_vk["HD+VFF"]["sources"]) == 2
    assert detail_by_vk["HD+VO"]["source_count"] == 1