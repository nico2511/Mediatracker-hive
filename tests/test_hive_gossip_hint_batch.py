"""Lot hint gossip : suffixe local contigu, jamais de saut pour un pair ancien."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.conftest import csrf, login
from tests.test_hive_t3_peer_http import CREATOR_URL, _enable
from tests.test_hive_t4 import _join_ruche, _settings_pair

from mediatracker.api.app import create_app
from mediatracker.db.models import HiveEntry
from mediatracker.hive.catalog import build_catalog_payload, sign_payload
from mediatracker.hive.crypto_id import hive_public_id_from_public_key
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_gossip import (
    GOSSIP_HINT_LIMIT,
    entries_for_gossip_hint,
    entries_page_for_peer,
    ingest_signed_entries,
    version_vector_for_ruche,
)
from mediatracker.hive.peer_gossip_vectors import (
    load_peer_gossip_vectors,
    record_peer_gossip_vectors,
)
from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_local_entries(db, settings, *, ruche_id: bytes, count: int) -> str:
    pk = load_private_key(settings)
    assert pk is not None
    origin = hive_public_id_from_public_key(pk.public_key())
    for seq in range(1, count + 1):
        cle = {"type": "movie", "tmdb_id": 920000 + seq, "version": "1080p+fr"}
        fiche = {"type": "movie", "tmdb_id": 920000 + seq, "title": f"HINT-{seq}", "year": 2099}
        payload = build_catalog_payload(
            ruche_id=ruche_id,
            origin_instance_id=origin,
            seq=seq,
            cle=cle,
            fiche=fiche,
            sha256_hex="d" * 64,
            file_id=bytes([seq % 256]) * 16,
            etat="disponible",
        )
        sig = sign_payload(pk, payload)
        ingest_signed_entries(
            db,
            ruche_id=ruche_id,
            items=[{"payload": payload, "signature_b64": sig}],
            now=_utc_now(),
        )
    db.commit()
    return origin


def _entry_count(db, *, ruche_id: bytes, origin: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(HiveEntry)
            .where(
                HiveEntry.ruche_id == ruche_id,
                HiveEntry.origin_instance_id == origin,
            )
        )
        or 0
    )


def _hint_seqs(items: list) -> list[int]:
    return [int(item["payload"]["seq"]) for item in items]


def _batch_is_safe(items: list, peer_seq: int) -> bool:
    if not items:
        return True
    seqs = _hint_seqs(items)
    return seqs[0] == peer_seq + 1 and seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_hint_batch_empty_when_peer_unknown_and_lag_above_limit(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://hint.exemple.fr"}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    db = client.app.state.session_factory()
    total = GOSSIP_HINT_LIMIT + 8
    origin = _seed_local_entries(db, client.app.state.settings, ruche_id=ruche_id, count=total)

    stale_page = entries_page_for_peer(db, ruche_id=ruche_id, peer_vector={}, limit=GOSSIP_HINT_LIMIT)
    assert min(_hint_seqs(stale_page.entries)) == 1
    assert max(_hint_seqs(stale_page.entries)) < total

    assert entries_for_gossip_hint(db, ruche_id=ruche_id, local_origin_id=origin) == []
    assert entries_for_gossip_hint(db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={}) == []
    assert entries_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={origin: 0}
    ) == []
    db.close()


def test_hint_batch_sends_contiguous_recent_when_peer_is_caught_up(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://hint.exemple.fr"}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    db = client.app.state.session_factory()
    total = GOSSIP_HINT_LIMIT + 8
    origin = _seed_local_entries(db, client.app.state.settings, ruche_id=ruche_id, count=total)

    one = entries_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={origin: total - 1}
    )
    assert _hint_seqs(one) == [total]
    assert all(item["payload"]["origine"] == origin for item in one)

    few = entries_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={origin: total - 3}
    )
    assert _hint_seqs(few) == [total - 2, total - 1, total]
    assert _batch_is_safe(few, total - 3)

    at_limit = entries_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={origin: total - GOSSIP_HINT_LIMIT}
    )
    assert _hint_seqs(at_limit) == list(range(total - GOSSIP_HINT_LIMIT + 1, total + 1))
    db.close()


def test_hint_batch_never_has_a_hole_for_old_ingest(client):
    """Un pair ancien (qui accepte encore les sauts) ne reçoit jamais un lot avec trou."""
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://hint.exemple.fr"}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    db = client.app.state.session_factory()
    total = GOSSIP_HINT_LIMIT + 8
    origin = _seed_local_entries(db, client.app.state.settings, ruche_id=ruche_id, count=total)

    for peer_seq in (0, 1, total - GOSSIP_HINT_LIMIT - 1, total - 1, total, total + 2):
        items = entries_for_gossip_hint(
            db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={origin: peer_seq}
        )
        assert _batch_is_safe(items, peer_seq), (peer_seq, _hint_seqs(items))
    db.close()


def test_empty_peer_with_small_catalog_gets_prefix_from_one(client):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://hint.exemple.fr"}},
        headers=csrf(client),
    )
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    db = client.app.state.session_factory()
    total = 3
    origin = _seed_local_entries(db, client.app.state.settings, ruche_id=ruche_id, count=total)
    items = entries_for_gossip_hint(db, ruche_id=ruche_id, local_origin_id=origin, peer_vector={})
    assert _hint_seqs(items) == [1, 2, 3]
    db.close()


@pytest.fixture
def hint_pair(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "hint_a"))
    app_b = create_app(_settings_pair(tmp_path, "hint_b"))
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()
        set_peer_asgi_app_for_tests(None)


def test_old_peer_that_accepts_gaps_does_not_get_a_hole_from_hint(hint_pair):
    app_a, app_b = hint_pair
    invitee_url = "https://mtr-test.exemple.fr"
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", invitee_url)
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", invitee_url)
        ruche_id = bytes.fromhex(rid)
        total = GOSSIP_HINT_LIMIT + 8

        db_a = app_a.state.session_factory()
        origin = _seed_local_entries(db_a, app_a.state.settings, ruche_id=ruche_id, count=total)
        # Pair vide / vecteur inconnu, catalogue > 50 : pas de lot.
        hint_unknown = entries_for_gossip_hint(db_a, ruche_id=ruche_id, local_origin_id=origin)
        assert hint_unknown == []
        full_page = entries_page_for_peer(db_a, ruche_id=ruche_id, peer_vector={}, limit=total + 1)
        db_a.close()

        db_b = app_b.state.session_factory()
        accepted = ingest_signed_entries(
            db_b,
            ruche_id=ruche_id,
            items=hint_unknown,
            now=_utc_now(),
            allow_seq_gap=True,
        )
        db_b.commit()
        assert accepted == 0
        assert version_vector_for_ruche(db_b, ruche_id) == {}
        assert _entry_count(db_b, ruche_id=ruche_id, origin=origin) == 0

        accepted_pull = ingest_signed_entries(
            db_b, ruche_id=ruche_id, items=full_page.entries, now=_utc_now()
        )
        db_b.commit()
        assert accepted_pull == total
        assert version_vector_for_ruche(db_b, ruche_id)[origin] == total
        assert _entry_count(db_b, ruche_id=ruche_id, origin=origin) == total
        db_b.close()


def test_caught_up_peer_receives_only_the_new_seq(hint_pair):
    app_a, app_b = hint_pair
    invitee_url = "https://mtr-test.exemple.fr"
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", CREATOR_URL)
        _enable(cb, "bb", invitee_url)
        login(ca)
        rid = ca.post("/api/v1/hive/ruches", headers=csrf(ca)).json()["ruche_id_hex"]
        creator_id = ca.get("/api/v1/hive/identity").json()["instance_id"]
        peer_id = cb.get("/api/v1/hive/identity").json()["instance_id"]
        _join_ruche(ca, cb, rid, creator_id, "bb", invitee_url)
        ruche_id = bytes.fromhex(rid)
        total = GOSSIP_HINT_LIMIT + 8

        db_a = app_a.state.session_factory()
        origin = _seed_local_entries(db_a, app_a.state.settings, ruche_id=ruche_id, count=total)
        prefix = entries_page_for_peer(
            db_a, ruche_id=ruche_id, peer_vector={}, limit=total - 1
        ).entries
        record_peer_gossip_vectors(
            db_a,
            ruche_id=ruche_id,
            peer_instance_id=peer_id,
            vector={origin: total - 1},
            signal_vector={},
            now=_utc_now(),
        )
        stored, _ = load_peer_gossip_vectors(db_a, ruche_id=ruche_id, peer_instance_id=peer_id)
        hint_items = entries_for_gossip_hint(
            db_a, ruche_id=ruche_id, local_origin_id=origin, peer_vector=stored
        )
        assert _hint_seqs(hint_items) == [total]
        db_a.close()

        db_b = app_b.state.session_factory()
        assert ingest_signed_entries(db_b, ruche_id=ruche_id, items=prefix, now=_utc_now()) == total - 1
        db_b.commit()
        accepted = ingest_signed_entries(
            db_b,
            ruche_id=ruche_id,
            items=hint_items,
            now=_utc_now(),
            allow_seq_gap=True,
        )
        db_b.commit()
        assert accepted == 1
        assert version_vector_for_ruche(db_b, ruche_id)[origin] == total
        assert _entry_count(db_b, ruche_id=ruche_id, origin=origin) == total
        db_b.close()
