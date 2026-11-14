"""Régression : comptage sources_json (liste) dans l'API catalogue Hive."""

from __future__ import annotations

import json
import json as jsonlib

from sqlalchemy import select
from tests.conftest import catalog_min_logical_video_bytes, login
from tests.test_hive_t6 import NOW, _enable, _ingest, _ruche, _seed_catalogue_row

from mediatracker.api.v1 import hive_ui
from mediatracker.db.models import HiveCatalogue, HiveEntry, Media

pytest_plugins = ["tests.test_hive_t1"]


def test_stable_tmdb_poster_picks_lexicographic_min(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    tmdb_id = 70100
    cle = {"type": "film", "tmdb_id": tmdb_id}
    late = "https://image.tmdb.org/t/p/w500/film-test-z.jpg"
    early = "https://image.tmdb.org/t/p/w342/film-test-a.jpg"
    try:
        for poster, seq in ((late, 1), (early, 2)):
            entry = HiveEntry(
                ruche_id=rid,
                origin_instance_id=f"hv1test-seq-{seq}-test-test-test-test",
                seq=seq,
                cle_json=jsonlib.dumps({**cle, "version": "HD+VFF"}, sort_keys=True),
                fiche_json=jsonlib.dumps(
                    {"type": "film", "tmdb_id": tmdb_id, "title": "Film-Test", "poster_url": poster},
                    sort_keys=True,
                ),
                sha256_hex=f"{seq:064x}",
                file_id=bytes([seq]) * 16,
                etat="disponible",
            )
            db.add(entry)
        db.commit()
        assert hive_ui._stable_tmdb_poster_for_cle(db, cle) == early
    finally:
        db.close()


def test_catalogue_version_extra_fields(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 70101, "version": "1080p+VFF"}
        _seed_catalogue_row(
            db,
            cle=cle,
            version="1080p+VFF",
            sha="a" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
            taille=5_000_000,
        )
        media = Media(type="film", title="Film-Test", year=2099, tmdb_id=70101, pref_quality="HD", pref_language="VFF")
        db.add(media)
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue").json()
    work = next(w for w in page["items"] if w["tmdb_id"] == 70101)
    ver = work["versions"][0]
    assert ver["quality"] == "1080p"
    assert ver["language"] == "VFF"
    assert ver["size_bytes"] == 5_000_000
    assert ver["ruche_count"] == 1
    assert ver["recommended"] is True


def test_parse_json_dict_unchanged():
    assert hive_ui._parse_json('{"a": 1}') == {"a": 1}
    assert hive_ui._parse_json("not-json") == {}
    assert hive_ui._parse_json("[1, 2]") == {}


def test_parse_sources_json_list():
    raw = json.dumps([{"sha256": "a" * 64}, "skip", {"x": 1}], separators=(",", ":"))
    assert hive_ui._parse_sources_json(raw) == [{"sha256": "a" * 64}, {"x": 1}]
    assert hive_ui._parse_sources_json("") == []
    assert hive_ui._parse_sources_json("broken") == []
    assert hive_ui._parse_sources_json("{}") == []
    assert hive_ui._parse_sources_json('{"not": "a list"}') == []


def test_catalogue_source_count_after_ingest(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    try:
        _ingest(
            db,
            settings,
            ruche_id=rid,
            tmdb_id=8802,
            season=1,
            episode=2,
            version="HD+VFF",
            sha="b" * 64,
            joignable=True,
            seq=1,
            size=catalog_min_logical_video_bytes(),
        )
        from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store

        refresh_hive_catalogue_store(db, now=NOW)
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue").json()
    assert page["items"]
    work = page["items"][0]
    assert work["disponible"] is True
    assert len(work["versions"]) == 1
    assert work["versions"][0]["source_count"] == 1
    assert work["versions"][0]["disponible"] is True


def test_catalogue_absent_corrupt_sources(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 70001, "version": "HD+VO"}
        _seed_catalogue_row(
            db,
            cle=cle,
            version="HD+VO",
            sha="c" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
            taille=0,
        )
        row = db.scalar(select(HiveCatalogue).where(HiveCatalogue.version_key == "HD+VO").limit(1))
        assert row is not None
        row.sources_json = "not-json"
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue").json()
    work = next(w for w in page["items"] if w["tmdb_id"] == 70001)
    assert work["disponible"] is True
    assert work["versions"][0]["source_count"] == 0


def test_catalogue_multiple_sources_and_versions(client):
    login(client)
    _enable(client)
    rid = _ruche(client)
    db = client.app.state.session_factory()
    try:
        cle = {"type": "film", "tmdb_id": 70002, "version": "HD+VFF"}
        _seed_catalogue_row(
            db,
            cle=cle,
            version="HD+VFF",
            sha="d" * 64,
            joignable=True,
            ruche_hex=rid.hex(),
            taille=0,
        )
        _seed_catalogue_row(
            db,
            cle={**cle, "version": "HD+VO"},
            version="HD+VO",
            sha="e" * 64,
            joignable=False,
            ruche_hex=rid.hex(),
            taille=0,
        )
        row = db.scalar(select(HiveCatalogue).where(HiveCatalogue.version_key == "HD+VFF").limit(1))
        assert row is not None
        sources = json.loads(row.sources_json)
        sources.append(
            {
                "ruche_id_hex": "ff" * 16,
                "origine": "hv1test-test-test-test-test-test-test",
                "file_id_hex": "ee" * 16,
                "sha256": "f" * 64,
                "joignable": True,
            }
        )
        row.sources_json = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        db.commit()
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue").json()
    work = next(w for w in page["items"] if w["tmdb_id"] == 70002)
    by_vk = {v["version_key"]: v for v in work["versions"]}
    assert by_vk["HD+VFF"]["source_count"] == 2
    assert by_vk["HD+VFF"]["disponible"] is True
    assert by_vk["HD+VO"]["source_count"] == 1
    assert by_vk["HD+VO"]["disponible"] is False
    assert work["disponible"] is True
