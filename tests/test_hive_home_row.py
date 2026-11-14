"""Accueil : rangée Dernières dispos Hive."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tests.conftest import login
from tests.test_hive_t6 import _enable, _ruche, _seed_catalogue_row

from mediatracker.db.models import HiveEntry, Media, MediaItem, Setting
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import HIVE_ENABLED_KEY

pytest_plugins = ["tests.test_hive_t6"]


def test_dashboard_hive_disabled_returns_empty_hive_dispos(client):
    login(client)
    board = client.get("/api/v1/dashboard").json()
    assert board.get("hive_dispos") == []


def test_dashboard_hive_dispos_hide_owned(client, app):
    login(client)
    _enable(client)
    db = app.state.session_factory()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    try:
        db.merge(Setting(key=HIVE_ENABLED_KEY, value="true", is_secret=False, updated_at=now))
        rid = _ruche(client).hex()
        owned_cle = {"type": "film", "tmdb_id": 88001, "version": "HD+VFF"}
        free_cle = {"type": "film", "tmdb_id": 88002, "version": "HD+VFF"}
        _seed_catalogue_row(
            db, cle=owned_cle, version="HD+VFF", sha="d" * 64, joignable=True, ruche_hex=rid, taille=0
        )
        _seed_catalogue_row(
            db, cle=free_cle, version="HD+VFF", sha="e" * 64, joignable=True, ruche_hex=rid, taille=0
        )
        origin = "hv1test-test-test-test-test-test-test"
        for cle, sha, ts in (
            (owned_cle, "d" * 64, now),
            (free_cle, "e" * 64, now + timedelta(minutes=1)),
        ):
            fiche = {"type": "film", "tmdb_id": cle["tmdb_id"], "title": "Démo", "year": 2099}
            db.add(
                HiveEntry(
                    ruche_id=bytes.fromhex(rid),
                    origin_instance_id=origin,
                    seq=1,
                    created_at=ts,
                    cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    sha256_hex=sha,
                    file_id=b"\xcc" * 16,
                    etat="disponible",
                    payload_json="{}",
                )
            )
        media = Media(type="film", title="Possédé", year=2099, tmdb_id=88001, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, state="downloaded")
        db.add(item)
        db.commit()
    finally:
        db.close()

    board = client.get("/api/v1/dashboard").json()
    dispos = board.get("hive_dispos") or []
    assert any(c.get("tmdb_id") == 88002 for c in dispos)
    assert all(c.get("tmdb_id") != 88001 for c in dispos)
    assert all(c.get("hive_work_key") for c in dispos)


def test_dashboard_hive_dispos_order_and_limit(client, app):
    login(client)
    _enable(client)
    db = app.state.session_factory()
    base = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    try:
        db.merge(
            Setting(
                key=HIVE_ENABLED_KEY,
                value="true",
                is_secret=False,
                updated_at=base,
            )
        )
        rid = _ruche(client).hex()
        origin = "hv1test-test-test-test-test-test-test"
        for i in range(25):
            tmdb_id = 89100 + i
            cle = {"type": "film", "tmdb_id": tmdb_id, "version": "HD+VFF"}
            sha = f"{i:064x}"
            _seed_catalogue_row(
                db,
                cle=cle,
                version="HD+VFF",
                sha=sha,
                joignable=True,
                ruche_hex=rid,
                taille=0,
            )
            fiche = {"type": "film", "tmdb_id": tmdb_id, "title": f"Œuvre {i}", "year": 2099}
            db.add(
                HiveEntry(
                    ruche_id=bytes.fromhex(rid),
                    origin_instance_id=origin,
                    seq=i + 1,
                    created_at=base + timedelta(hours=i),
                    cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    sha256_hex=sha,
                    file_id=bytes.fromhex(f"{i:032x}"),
                    etat="disponible",
                    payload_json="{}",
                )
            )
        db.commit()
    finally:
        db.close()

    dispos = client.get("/api/v1/dashboard").json().get("hive_dispos") or []
    assert len(dispos) == 20
    tmdb_ids = [c["tmdb_id"] for c in dispos]
    expected = list(range(89124, 89104, -1))
    assert tmdb_ids == expected
    assert _stable_work_key({"type": "film", "tmdb_id": 89124, "version": "HD+VFF"}) == dispos[0]["hive_work_key"]
