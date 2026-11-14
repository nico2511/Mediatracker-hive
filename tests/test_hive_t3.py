"""Hive T3 : ruches, invitations, membres, exclusion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_hive_t2 import _enable_hive, _origin_id

from mediatracker.db.models import HiveEntry, HiveInvitation, HiveMember
from mediatracker.hive.access import is_active_member
from mediatracker.hive.crypto_id import (
    build_name_document,
    generate_keypair,
    hive_public_id_from_public_key,
    public_key_to_pem,
    sign_document,
)
from mediatracker.hive.invitations import (
    create_invitation,
    revoke_invitation,
    validate_invitation_usable,
)
from mediatracker.hive.member_list import (
    MemberListError,
    apply_member_list,
    build_member_list_payload,
    member_list_for_ruche_a_only,
    sign_member_list,
)
from mediatracker.hive.membership import (
    accept_join_pending,
    begin_join,
    exclude_member,
    local_instance_identity,
    preview_invitation_link,
)

NOW = datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)


def _second_invitee(name: str = "zy"):
    pk, pub = generate_keypair()
    iid = hive_public_id_from_public_key(pub)
    doc = build_name_document(instance_id=iid, name=name, signed_at=NOW)
    sig = sign_document(pk, doc)
    return pk, iid, doc, sig, public_key_to_pem(pub).decode("utf-8")


def _create_ruche_api(client):
    login(client)
    r = client.post("/api/v1/hive/ruches", headers=csrf(client))
    assert r.status_code == 201, r.text
    return r.json()["ruche_id_hex"]


def test_create_ruche_requires_public_address(client):
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": ""}},
        headers=csrf(client),
    )
    assert r.status_code == 200
    r2 = client.post("/api/v1/hive/ruches", headers=csrf(client))
    assert r2.status_code == 422


def test_invitation_expired_revoked_reused(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    pk, creator_id, doc, sig_b64, pem, address = local_instance_identity(db, settings)
    row, link, token = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid),
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=NOW,
    )
    db.commit()
    inv = db.get(HiveInvitation, row.id)
    inv.expires_at = NOW - timedelta(hours=1)
    db.commit()
    with pytest.raises(ValueError, match="expired"):
        preview_invitation_link(db, settings, link=link, now=NOW)

    inv.expires_at = NOW + timedelta(hours=24)
    inv.consumed_at = None
    inv.revoked_at = None
    db.commit()
    revoke_invitation(db, invitation_id=row.id, creator_id=creator_id, now=NOW)
    db.commit()
    with pytest.raises(ValueError, match="revoked"):
        validate_invitation_usable(db.get(HiveInvitation, row.id), now=NOW)

    inv2, link2, token2 = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid),
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=NOW,
    )
    db.commit()
    ipk, _, _, _, _ = _second_invitee("mn")
    begin_join(
        db, settings, link=link2, invitee_private_key=ipk, invitee_name="mn", invitee_public_address="", now=NOW
    )
    inv2_row = db.get(HiveInvitation, inv2.id)
    inv2_row.consumed_at = NOW
    db.commit()
    with pytest.raises(ValueError, match="consumed"):
        validate_invitation_usable(inv2_row, now=NOW)


def test_pull_only_invitee_joins(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    pk, creator_id, _, _, pem, address = local_instance_identity(db, settings)
    _, link, _ = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid),
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=NOW,
    )
    db.commit()
    ipk, iid, _, _, _ = _second_invitee("po")
    code, _, pending_id = begin_join(
        db, settings, link=link, invitee_private_key=ipk, invitee_name="po", invitee_public_address="", now=NOW
    )
    accept_join_pending(db, settings, pending_id=pending_id, now=NOW)
    db.commit()
    member = db.scalar(select(HiveMember).where(HiveMember.member_instance_id == iid).limit(1))
    assert member is not None
    assert member.public_address == ""
    assert code and len(code) == 8


def test_two_distinct_keys_same_ruche(client, app, settings):
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
        now=NOW + timedelta(minutes=1),
    )
    db.commit()
    k1, id1, *_ = _second_invitee("aa")
    k2, id2, *_ = _second_invitee("bb")
    _, _, p1 = begin_join(
        db, settings, link=link1, invitee_private_key=k1, invitee_name="aa",
        invitee_public_address="https://mtr.exemple.fr", now=NOW,
    )
    _, _, p2 = begin_join(
        db, settings, link=link2, invitee_private_key=k2, invitee_name="bb",
        invitee_public_address="https://mtr-test.exemple.fr", now=NOW,
    )
    accept_join_pending(db, settings, pending_id=p1, now=NOW)
    db.commit()
    accept_join_pending(db, settings, pending_id=p2, now=NOW)
    db.commit()
    assert id1 != id2
    members = db.scalars(select(HiveMember).where(HiveMember.ruche_id == bytes.fromhex(rid))).all()
    assert len(list(members)) == 3


def test_ruche_isolation_ab(client, app, settings):
    _enable_hive(client)
    rid_a = _create_ruche_api(client)
    rid_b = _create_ruche_api(client)
    db = app.state.session_factory()
    ma, mb = member_list_for_ruche_a_only(db, bytes.fromhex(rid_a), bytes.fromhex(rid_b))
    assert len(ma) == 1
    assert len(mb) == 1
    ids_a = {m["id"] for m in ma}
    ids_b = {m["id"] for m in mb}
    assert ids_a.isdisjoint(ids_b) or ids_a == ids_b  # même créateur, listes séparées
    assert not any(bytes.fromhex(rid_b).hex() in str(m) for m in ma)


def test_exclude_removes_catalog_access(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    rid_hex = _create_ruche_api(client)
    rid = bytes.fromhex(rid_hex)
    pk, creator_id, _, _, pem, address = local_instance_identity(db, settings)
    _, link, _ = create_invitation(
        db,
        ruche_id=rid,
        creator_id=creator_id,
        creator_address=address,
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=NOW,
    )
    db.commit()
    ipk, target, _, _, _ = _second_invitee("vx")
    _, _, pending_id = begin_join(
        db, settings, link=link, invitee_private_key=ipk, invitee_name="vx", invitee_public_address="", now=NOW
    )
    accept_join_pending(db, settings, pending_id=pending_id, now=NOW)
    db.commit()
    entry = HiveEntry(
        ruche_id=rid,
        origin_instance_id=target,
        seq=1,
        etat="disponible",
        sha256_hex="a" * 64,
        payload_json="{}",
        signature_b64="e30=",
    )
    db.add(entry)
    db.commit()
    assert is_active_member(db, ruche_id=rid, instance_id=target)
    exclude_member(db, settings, ruche_id=rid, target_instance_id=target, now=NOW)
    db.commit()
    assert not is_active_member(db, ruche_id=rid, instance_id=target)
    rows = list(
        db.scalars(
            select(HiveEntry).where(
                HiveEntry.ruche_id == rid, HiveEntry.origin_instance_id == target, HiveEntry.etat == "disponible"
            )
        ).all()
    )
    assert rows == []


def test_reject_forged_name_and_rename(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    pk, creator_id, doc, sig_b64, pem, _ = local_instance_identity(db, settings)
    ruche_id = bytes.fromhex(rid)
    bad_pk, bad_pub = generate_keypair()
    bad_doc = build_name_document(instance_id=creator_id, name="evil", signed_at=NOW)
    bad_sig = sign_document(bad_pk, bad_doc)
    members = [
        {
            "id": creator_id,
            "public_key_pem": pem,
            "nom_doc": bad_doc,
            "nom_sig_b64": __import__("base64").b64encode(bad_sig).decode("ascii"),
            "address": "https://mtr.exemple.fr",
            "joined": NOW.isoformat().replace("+00:00", "Z"),
        }
    ]
    payload = build_member_list_payload(ruche_id=ruche_id, version=2, members=members)
    sig = sign_member_list(pk, payload)
    with pytest.raises(MemberListError, match="bad_name_signature"):
        apply_member_list(
            db,
            ruche_id=ruche_id,
            creator_id=creator_id,
            creator_public_key=pk.public_key(),
            payload=payload,
            signature_b64=sig,
            now=NOW,
        )

    good_members = [
        {
            "id": creator_id,
            "public_key_pem": pem,
            "nom_doc": doc,
            "nom_sig_b64": sig_b64,
            "address": "https://mtr.exemple.fr",
            "joined": NOW.isoformat().replace("+00:00", "Z"),
        }
    ]
    payload_ok = build_member_list_payload(ruche_id=ruche_id, version=2, members=good_members)
    apply_member_list(
        db,
        ruche_id=ruche_id,
        creator_id=creator_id,
        creator_public_key=pk.public_key(),
        payload=payload_ok,
        signature_b64=sign_member_list(pk, payload_ok),
        now=NOW,
    )
    doc2 = build_name_document(instance_id=creator_id, name="other", signed_at=NOW)
    sig2 = sign_document(pk, doc2)
    members2 = [
        {
            "id": creator_id,
            "public_key_pem": pem,
            "nom_doc": doc2,
            "nom_sig_b64": __import__("base64").b64encode(sig2).decode("ascii"),
            "address": "https://mtr.exemple.fr",
            "joined": NOW.isoformat().replace("+00:00", "Z"),
        }
    ]
    payload2 = build_member_list_payload(ruche_id=ruche_id, version=3, members=members2)
    with pytest.raises(MemberListError, match="identity_name_change_rejected"):
        apply_member_list(
            db,
            ruche_id=ruche_id,
            creator_id=creator_id,
            creator_public_key=pk.public_key(),
            payload=payload2,
            signature_b64=sign_member_list(pk, payload2),
            now=NOW,
        )


def test_reset_requires_new_invitation(client, app, settings):
    _enable_hive(client)
    rid = _create_ruche_api(client)
    db = app.state.session_factory()
    origin_before = _origin_id(settings)
    assert is_active_member(db, ruche_id=bytes.fromhex(rid), instance_id=origin_before)
    login(client)
    r = client.post("/api/v1/hive/identity/reset", json={"name": "cd"}, headers=csrf(client))
    assert r.status_code == 204
    db.expire_all()
    origin_after = _origin_id(settings)
    assert origin_before != origin_after
    assert not is_active_member(db, ruche_id=bytes.fromhex(rid), instance_id=origin_after)
