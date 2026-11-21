"""Remise à zéro du quota hebdomadaire d'un membre (propriétaire)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from tests.conftest import csrf
from tests.test_hive_t2 import _enable_hive
from tests.test_hive_t6 import _ruche

from mediatracker.db.models import Event, HiveEntry, HiveMember, HiveReceiveQuota, HiveRuche
from mediatracker.hive.transfer_settings import week_start_date

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
MEMBER_B = "hv1peerb-peer-peer-peer-peer-peerpeer"
USED = 99_400_000_000


def _path(rid: bytes, member: str) -> str:
    return f"/api/v1/hive/ruches/{rid.hex()}/members/{member}/quota/reset"


def _seed_quota_member(app, settings, *, rid: bytes, shares: bool) -> None:
    db = app.state.session_factory()
    try:
        if (
            db.scalar(
                select(HiveMember.id)
                .where(HiveMember.ruche_id == rid, HiveMember.member_instance_id == MEMBER_B)
                .limit(1)
            )
            is None
        ):
            db.add(
                HiveMember(
                    ruche_id=rid,
                    member_instance_id=MEMBER_B,
                    public_address="https://mtr.exemple.fr",
                )
            )
        ws = week_start_date(NOW)
        db.merge(
            HiveReceiveQuota(
                ruche_id=rid,
                member_instance_id=MEMBER_B,
                week_start=ws,
                bytes_used=USED,
            )
        )
        if shares:
            db.add(
                HiveEntry(
                    ruche_id=rid,
                    origin_instance_id=MEMBER_B,
                    seq=1,
                    cle_json='{"type":"movie","tmdb_id":900001,"version":"1080p"}',
                    fiche_json='{"type":"movie","tmdb_id":900001,"title":"Oeuvre fictive A"}',
                    sha256_hex="aa" * 32,
                    etat="disponible",
                )
            )
        db.commit()
    finally:
        db.close()


def test_member_quota_reset_success(client, app, settings, monkeypatch):
    from mediatracker.api import v1 as v1_api

    monkeypatch.setattr(v1_api.hive_ruches, "_now", lambda: NOW)
    _enable_hive(client)
    rid = _ruche(client)
    _seed_quota_member(app, settings, rid=rid, shares=False)
    r = client.post(_path(rid, MEMBER_B), headers=csrf(client))
    assert r.status_code == 204, r.text
    db = app.state.session_factory()
    try:
        ws = week_start_date(NOW)
        row = db.get(HiveReceiveQuota, (rid, MEMBER_B, ws))
        assert row is not None
        assert int(row.bytes_used) == 0
        evt = db.scalar(select(Event).where(Event.type == "hive.quota.reset").order_by(Event.id.desc()).limit(1))
        assert evt is not None
        assert MEMBER_B in str((evt.payload or {}).get("member_instance_id"))
    finally:
        db.close()


def test_member_quota_reset_forbidden_not_creator(client, app, settings, monkeypatch):
    from mediatracker.api import v1 as v1_api

    monkeypatch.setattr(v1_api.hive_ruches, "_now", lambda: NOW)
    _enable_hive(client)
    rid = _ruche(client)
    _seed_quota_member(app, settings, rid=rid, shares=False)
    db = app.state.session_factory()
    try:
        ruche = db.get(HiveRuche, rid)
        ruche.creator_instance_id = MEMBER_B
        db.commit()
    finally:
        db.close()
    r = client.post(_path(rid, MEMBER_B), headers=csrf(client))
    assert r.status_code == 403, r.text


def test_member_quota_reset_unknown_member(client, app, settings, monkeypatch):
    from mediatracker.api import v1 as v1_api

    monkeypatch.setattr(v1_api.hive_ruches, "_now", lambda: NOW)
    _enable_hive(client)
    rid = _ruche(client)
    r = client.post(_path(rid, "hv1missing-missing-missing-missing-missing"), headers=csrf(client))
    assert r.status_code == 404, r.text


def test_member_quota_reset_shares_in_ruche(client, app, settings, monkeypatch):
    from mediatracker.api import v1 as v1_api

    monkeypatch.setattr(v1_api.hive_ruches, "_now", lambda: NOW)
    _enable_hive(client)
    rid = _ruche(client)
    _seed_quota_member(app, settings, rid=rid, shares=True)
    r = client.post(_path(rid, MEMBER_B), headers=csrf(client))
    assert r.status_code == 422, r.text


def test_member_quota_reset_ruche_unknown(client):
    from tests.conftest import csrf

    _enable_hive(client)
    fake = "ff" * 16
    r = client.post(_path(bytes.fromhex(fake), MEMBER_B), headers=csrf(client))
    assert r.status_code in {404, 422}