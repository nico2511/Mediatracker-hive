"""Joignabilité catalogue : instance locale sans TTL gossip ; pairs distants inchangés."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login

from mediatracker.hive.hive_catalogue import build_merged_hive_catalogue, refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
from mediatracker.hive.peer_gossip import REACHABILITY_TTL_SECONDS, mark_origin_unreachable

NOW = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)
pytest_plugins = ["tests.test_hive_t5"]


def _enable(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://mtr.exemple.fr"}},
        headers=csrf(client),
    )


def _ruche(client) -> bytes:
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    return bytes.fromhex(rid)


def _add_disponible_entry(db, *, ruche_id: bytes, origin: str, tmdb_id: int = 88055) -> None:
    cle = {"type": "film", "tmdb_id": tmdb_id, "version": "HD+VFF"}
    fiche = {
        "type": "film",
        "tmdb_id": tmdb_id,
        "title": "Oeuvre fictive",
        "year": 2099,
        "taille": PLAUSIBLE_VIDEO_BYTES,
    }
    payload = {"taille": PLAUSIBLE_VIDEO_BYTES, "fiche": fiche}
    from mediatracker.db.models import HiveEntry

    db.add(
        HiveEntry(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=1,
            cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            sha256_hex="f" * 64,
            file_id=b"\x01" * 16,
            etat="disponible",
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    )


def test_local_origin_reachable_without_gossip_ttl(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    pk = load_private_key(settings)
    assert pk
    local_origin = hive_public_id_from_public_key(pk.public_key())
    stale = NOW - timedelta(seconds=REACHABILITY_TTL_SECONDS + 120)
    try:
        _add_disponible_entry(db, ruche_id=rid, origin=local_origin)
        mark_origin_unreachable(db, ruche_id=rid, origin_id=local_origin, now=stale)
        db.commit()
        merged = build_merged_hive_catalogue(db, now=NOW, settings=settings)
        src = merged[0]["versions"][0]["sources"][0]
        assert src["origine"] == local_origin
        assert src["joignable"] is True
        assert merged[0]["versions"][0]["disponible"] is True
        assert refresh_hive_catalogue_store(db, now=NOW, settings=settings) == 1
        db.commit()
    finally:
        db.close()


def test_remote_origin_still_requires_fresh_reachability(client):
    _enable(client)
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    rid = _ruche(client)
    remote = "hv1peer-peer-peer-peer-peer-peer-peer"
    stale = NOW - timedelta(seconds=REACHABILITY_TTL_SECONDS + 120)
    try:
        _add_disponible_entry(db, ruche_id=rid, origin=remote, tmdb_id=88056)
        mark_origin_unreachable(db, ruche_id=rid, origin_id=remote, now=stale)
        db.commit()
        merged = build_merged_hive_catalogue(db, now=NOW, settings=settings)
        src = merged[0]["versions"][0]["sources"][0]
        assert src["origine"] == remote
        assert src["joignable"] is False
        assert merged[0]["versions"][0]["disponible"] is False
    finally:
        db.close()
