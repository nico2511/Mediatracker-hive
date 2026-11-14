"""Refus et dédoublonnage des demandes d'adhésion Hive."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from tests.conftest import csrf
from tests.test_hive_t2 import _enable_hive
from tests.test_hive_t3 import NOW, _create_ruche_api, _second_invitee

from mediatracker.db.models import HiveJoinPending, HiveRuche
from mediatracker.hive.invitations import create_invitation
from mediatracker.hive.membership import (
    begin_join,
    local_instance_identity,
    open_pending_count_for_ruche,
    pending_for_ruche,
)

NOW2 = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


def _invite_link(db, settings, rid_hex: str, *, now: datetime = NOW) -> str:
    pk, creator_id, _, _, pem, address = local_instance_identity(db, settings)
    _, link, _ = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid_hex),
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=now,
    )
    db.commit()
    return link


def test_reject_pending_creator_only(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    link = _invite_link(db, settings, rid)
    ipk, _, *_ = _second_invitee("rj")
    _, _, pending_id = begin_join(
        db, settings, link=link, invitee_private_key=ipk, invitee_name="rj", invitee_public_address="", now=NOW
    )
    db.commit()
    assert len(pending_for_ruche(db, bytes.fromhex(rid))) == 1

    r_reject = client.post(
        "/api/v1/hive/join/reject",
        json={"pending_id": pending_id},
        headers=csrf(client),
    )
    assert r_reject.status_code == 204, r_reject.text
    assert pending_for_ruche(db, bytes.fromhex(rid)) == []

    pending = db.get(HiveJoinPending, pending_id)
    assert pending is not None
    assert pending.rejected_at is not None
    assert pending.consumed_at is None

    r_list = client.get(f"/api/v1/hive/join/pending/{rid}", headers=csrf(client))
    assert r_list.status_code == 200
    assert r_list.json() == []


def test_reject_pending_not_creator_forbidden(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    link = _invite_link(db, settings, rid)
    ipk, _, *_ = _second_invitee("nx")
    _, _, pending_id = begin_join(
        db, settings, link=link, invitee_private_key=ipk, invitee_name="nx", invitee_public_address="", now=NOW
    )
    db.commit()
    ruche = db.get(HiveRuche, bytes.fromhex(rid))
    assert ruche is not None
    ruche.creator_instance_id = "identifiant-inexistant-crea"
    db.commit()

    r_reject = client.post(
        "/api/v1/hive/join/reject",
        json={"pending_id": pending_id},
        headers=csrf(client),
    )
    assert r_reject.status_code == 403, r_reject.text


def test_begin_join_deduplicates_same_invitee(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    pk, creator_id, _, _, pem, address = local_instance_identity(db, settings)
    _, link1, _ = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid),
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=NOW,
    )
    _, link2, _ = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid),
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=NOW2,
    )
    db.commit()
    ipk, iid, *_ = _second_invitee("dd")
    _, _, p1 = begin_join(
        db, settings, link=link1, invitee_private_key=ipk, invitee_name="dd", invitee_public_address="", now=NOW
    )
    _, _, p2 = begin_join(
        db, settings, link=link2, invitee_private_key=ipk, invitee_name="dd", invitee_public_address="", now=NOW2
    )
    db.commit()
    assert p1 == p2
    count = db.scalar(
        select(func.count())
        .select_from(HiveJoinPending)
        .where(
            HiveJoinPending.ruche_id == bytes.fromhex(rid),
            HiveJoinPending.invitee_instance_id == iid,
            HiveJoinPending.consumed_at.is_(None),
            HiveJoinPending.rejected_at.is_(None),
        )
    )
    assert count == 1

    listed = client.get(f"/api/v1/hive/join/pending/{rid}", headers=csrf(client)).json()
    assert len(listed) == 1
    assert listed[0]["invitee_instance_id"] == iid


def _duplicate_open_pending(db, settings, *, template_id: int, now: datetime) -> int:
    """Simule un doublon historique (même ruche + même invité)."""
    template = db.get(HiveJoinPending, template_id)
    assert template is not None
    dup = HiveJoinPending(
        ruche_id=template.ruche_id,
        invitation_id=template.invitation_id,
        invitee_instance_id=template.invitee_instance_id,
        invitee_public_key_pem=template.invitee_public_key_pem,
        invitee_name_document_json=template.invitee_name_document_json,
        invitee_name_signature_b64=template.invitee_name_signature_b64,
        invitee_public_address=template.invitee_public_address,
        control_code=template.control_code,
        created_at=now,
    )
    db.add(dup)
    db.flush()
    return dup.id


def test_reject_closes_all_open_duplicates(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    link = _invite_link(db, settings, rid)
    ipk, iid, *_ = _second_invitee("dup")
    _, _, pending_id = begin_join(
        db, settings, link=link, invitee_private_key=ipk, invitee_name="dup", invitee_public_address="", now=NOW
    )
    dup_id = _duplicate_open_pending(db, settings, template_id=pending_id, now=NOW)
    db.commit()
    assert len(pending_for_ruche(db, bytes.fromhex(rid))) == 1
    open_count = db.scalar(
        select(func.count())
        .select_from(HiveJoinPending)
        .where(
            HiveJoinPending.ruche_id == bytes.fromhex(rid),
            HiveJoinPending.invitee_instance_id == iid,
            HiveJoinPending.consumed_at.is_(None),
            HiveJoinPending.rejected_at.is_(None),
        )
    )
    assert open_count == 2

    r_reject = client.post(
        "/api/v1/hive/join/reject",
        json={"pending_id": pending_id},
        headers=csrf(client),
    )
    assert r_reject.status_code == 204
    db.expire_all()
    assert pending_for_ruche(db, bytes.fromhex(rid)) == []
    assert open_pending_count_for_ruche(db, bytes.fromhex(rid)) == 0
    for pid in (pending_id, dup_id):
        row = db.get(HiveJoinPending, pid)
        assert row is not None and row.rejected_at is not None
    assert client.get(f"/api/v1/hive/join/pending/{rid}", headers=csrf(client)).json() == []
    ruches = client.get("/api/v1/hive/ruches", headers=csrf(client)).json()
    row = next(r for r in ruches if r["ruche_id_hex"] == rid)
    assert row["pending_join_count"] == 0


def test_accept_closes_duplicate_open_rows(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    now = datetime.now(timezone.utc)
    link = _invite_link(db, settings, rid, now=now)
    ipk, iid, *_ = _second_invitee("ac")
    _, _, pending_id = begin_join(
        db, settings, link=link, invitee_private_key=ipk, invitee_name="ac", invitee_public_address="", now=now
    )
    dup_id = _duplicate_open_pending(db, settings, template_id=pending_id, now=now)
    db.commit()

    r_accept = client.post(
        "/api/v1/hive/join/accept",
        json={"pending_id": pending_id},
        headers=csrf(client),
    )
    assert r_accept.status_code == 204, r_accept.text
    db.expire_all()
    assert pending_for_ruche(db, bytes.fromhex(rid)) == []
    assert open_pending_count_for_ruche(db, bytes.fromhex(rid)) == 0
    accepted = db.get(HiveJoinPending, pending_id)
    duplicate = db.get(HiveJoinPending, dup_id)
    assert accepted is not None and accepted.consumed_at is not None
    assert duplicate is not None and duplicate.rejected_at is not None
    assert client.get(f"/api/v1/hive/join/pending/{rid}", headers=csrf(client)).json() == []
