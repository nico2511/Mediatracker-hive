"""Mini-fiche dans le signal cherche (affichage Recherché sans TMDB)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.db.models import HiveMember, HiveSignal, Media, MediaItem
from mediatracker.hive.catalog import sign_payload
from mediatracker.hive.crypto_id import public_key_to_pem
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.signal import (
    build_signal_payload,
    cle_hash,
    ingest_signed_signals,
    next_signal_seq,
    publish_cherche_for_item,
    verify_remote_signal,
)
from mediatracker.hive.signal_mini_fiche import build_cherche_mini_fiche, mini_fiche_display_meta
from mediatracker.hive.signal_wanted import _work_key_for_cle, list_wanted_requests

NOW = datetime(2026, 11, 14, 14, 0, tzinfo=timezone.utc)
REMOTE = "peerMiniFichePlateau9"


def _utc_now() -> datetime:
    return NOW


def _film_cle(tmdb_id: int = 882001) -> dict:
    return {"type": "movie", "tmdb_id": tmdb_id, "version": "1080p+fr"}


def test_cherche_emit_stores_mini_fiche(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media = Media(
            type="film",
            title="Atelier Zéphyr",
            year=2097,
            tmdb_id=882001,
            followed=True,
            poster_url="https://image.tmdb.org/t/p/w500/affiche-demo-zephyr.jpg",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None)
        db.add(item)
        db.flush()
        mini = build_cherche_mini_fiche(media, item)
        assert mini["title"] == "Atelier Zéphyr"
        assert mini.get("poster_path") == "/affiche-demo-zephyr.jpg"
        assert mini_fiche_display_meta(mini, media_type="film")["poster_url"] == (
            "https://image.tmdb.org/t/p/w500/affiche-demo-zephyr.jpg"
        )
        assert publish_cherche_for_item(db, settings, media=media, item=item, now=_utc_now())
        row = db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1))
        assert row is not None
        assert row.mini_fiche_json is not None
        stored = json.loads(row.mini_fiche_json or "{}")
        assert stored.get("poster_path") == "/affiche-demo-zephyr.jpg"
        payload = json.loads(row.payload_json or "{}")
        assert payload.get("mini_fiche", {}).get("title") == "Atelier Zéphyr"
        assert payload.get("mini_fiche", {}).get("poster_path") == "/affiche-demo-zephyr.jpg"
        assert cle_hash(json.loads(row.cle_json or "{}")) == row.cle_hash
        db.commit()
    finally:
        db.close()


def test_wanted_list_prefers_mini_fiche_without_tmdb(client, settings, monkeypatch):
    _enable_hive(client)
    def _forbidden(*_a, **_kw):
        raise AssertionError("TMDB ne doit pas être appelé quand la mini-fiche est présente")

    monkeypatch.setattr("mediatracker.hive.signal_wanted.fetch_wanted_tmdb_meta", _forbidden)

    db = client.app.state.session_factory()
    try:
        rid = _join_fixture_ruche(db, _origin_id(settings))
        pk = load_private_key(settings)
        assert pk is not None
        cle = _film_cle()
        mini = {
            "title": "Atelier Zéphyr",
            "year": 2097,
            "type": "film",
            "poster_path": "/t/p/w92/demo-zephyr.jpg",
        }
        expected_poster = "https://image.tmdb.org/t/p/w500/demo-zephyr.jpg"
        seq = next_signal_seq(db, rid, REMOTE)
        payload = build_signal_payload(
            ruche_id=rid,
            origin_instance_id=REMOTE,
            seq=seq,
            signal="cherche",
            cle=cle,
            mini_fiche=mini,
        )
        sig = sign_payload(pk, payload)
        db.add(
            HiveSignal(
                ruche_id=rid,
                origin_instance_id=REMOTE,
                seq=seq,
                created_at=_utc_now(),
                signal="cherche",
                cle_hash=cle_hash(cle),
                cle_json=json.dumps(cle),
                payload_json=json.dumps(payload),
                signature_b64=sig,
                mini_fiche_json=json.dumps(mini),
            )
        )
        db.flush()
        rows = list_wanted_requests(db, settings, now=_utc_now())
        assert len(rows) == 1
        assert rows[0]["title"] == "Atelier Zéphyr"
        assert rows[0]["year"] == 2097
        assert rows[0]["poster_url"] == expected_poster
    finally:
        db.close()


def test_mini_fiche_poster_url_from_bare_path():
    meta = mini_fiche_display_meta(
        {"title": "Atelier", "type": "film", "poster_path": "/fichier-nu-demo.jpg"},
        media_type="film",
    )
    assert meta["poster_url"] == "https://image.tmdb.org/t/p/w500/fichier-nu-demo.jpg"


def test_mini_fiche_outside_cle_keeps_work_key():
    cle = _film_cle(882003)
    wk = _stable_work_key({"type": "film", "tmdb_id": 882003})
    assert _work_key_for_cle(cle) == wk
    payload = build_signal_payload(
        ruche_id=bytes([0] * 15 + [1]),
        origin_instance_id="origineTest",
        seq=1,
        signal="cherche",
        cle=cle,
        mini_fiche={"title": "Forge Bêta", "type": "film"},
    )
    assert payload["cle"] == cle
    assert _work_key_for_cle(payload["cle"]) == wk


def test_ingest_accepts_signed_payload_with_mini_fiche(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        db.flush()
        pk = load_private_key(settings)
        assert pk is not None
        pem = public_key_to_pem(pk.public_key()).decode("utf-8")
        member = db.scalar(
            select(HiveMember).where(HiveMember.ruche_id == rid, HiveMember.member_instance_id == origin).limit(1)
        )
        assert member is not None
        member.public_key_pem = pem
        db.flush()
        cle = _film_cle(882002)
        mini = {"title": "Forge Bêta", "year": 2096, "type": "film"}
        seq = next_signal_seq(db, rid, origin)
        payload = build_signal_payload(
            ruche_id=rid,
            origin_instance_id=origin,
            seq=seq,
            signal="cherche",
            cle=cle,
            mini_fiche=mini,
        )
        sig = sign_payload(pk, payload)
        verify_remote_signal(db, ruche_id=rid, payload=payload, signature_b64=sig)
        n = ingest_signed_signals(
            db,
            ruche_id=rid,
            items=[{"payload": payload, "signature_b64": sig}],
            now=_utc_now(),
        )
        assert n == 1
        row = db.scalar(
            select(HiveSignal).where(HiveSignal.origin_instance_id == origin, HiveSignal.seq == seq).limit(1)
        )
        assert row is not None
        assert json.loads(row.mini_fiche_json or "{}")["title"] == "Forge Bêta"
    finally:
        db.close()


def test_legacy_signal_without_mini_fiche_uses_tmdb_fallback(client, settings, monkeypatch):
    _enable_hive(client)
    from mediatracker.hive.wanted_tmdb import clear_wanted_tmdb_cache

    clear_wanted_tmdb_cache()

    def _fetch(db, settings, *, media_type, tmdb_id, now=None):
        return {"title": "Repli TMDB fictif", "year": 2095, "poster_url": None}

    monkeypatch.setattr("mediatracker.hive.signal_wanted.fetch_wanted_tmdb_meta", _fetch)

    db = client.app.state.session_factory()
    try:
        rid = _join_fixture_ruche(db, _origin_id(settings))
        pk = load_private_key(settings)
        assert pk is not None
        cle = _film_cle(882004)
        seq = next_signal_seq(db, rid, REMOTE)
        payload = build_signal_payload(
            ruche_id=rid,
            origin_instance_id=REMOTE,
            seq=seq,
            signal="cherche",
            cle=cle,
        )
        sig = sign_payload(pk, payload)
        db.add(
            HiveSignal(
                ruche_id=rid,
                origin_instance_id=REMOTE,
                seq=seq,
                created_at=_utc_now(),
                signal="cherche",
                cle_hash=cle_hash(cle),
                cle_json=json.dumps(cle),
                payload_json=json.dumps(payload),
                signature_b64=sig,
                mini_fiche_json=None,
            )
        )
        db.flush()
        rows = list_wanted_requests(db, settings, now=_utc_now())
        assert rows[0]["title"] == "Repli TMDB fictif"
    finally:
        db.close()