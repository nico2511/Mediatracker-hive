"""Échanges pair : gossip paginé et listes de membres (client HTTP signé)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveRuche
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.hive.crypto_id import hive_public_id_from_public_key, public_key_from_pem
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.member_list import (
    MemberListError,
    apply_member_list_as_peer,
    latest_member_list_version,
)
from mediatracker.hive.membership import (
    creator_member_public_key_pem,
    purge_entries_for_removed_members,
)
from mediatracker.hive.peer_client import fetch_member_list, gossip_sync_once
from mediatracker.hive.peer_gossip import (
    GOSSIP_PAGE_DEFAULT,
    ingest_signed_entries,
    record_reachability,
    signals_page_for_peer,
    version_vector_for_ruche,
)
from mediatracker.hive.peer_gossip_vectors import load_peer_gossip_vectors, record_peer_gossip_vectors
from mediatracker.hive.peer_info import build_local_instance_snapshot, ingest_peer_info
from mediatracker.hive.signal import ingest_signed_signals, signal_version_vector_for_ruche
from mediatracker.hive.transfer_load import build_local_load_snapshot, ingest_peer_load

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GossipCursor:
    origin: str
    after_seq: int

    def as_dict(self) -> dict[str, Any]:
        return {"origin": self.origin, "after_seq": self.after_seq}

    @staticmethod
    def from_dict(raw: object) -> GossipCursor | None:
        if not isinstance(raw, dict):
            return None
        origin = raw.get("origin")
        after = raw.get("after_seq")
        if not isinstance(origin, str) or not isinstance(after, int):
            return None
        return GossipCursor(origin=origin, after_seq=after)


def pull_member_list_from_peer(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    peer_base_url: str,
    now: datetime,
) -> bool:
    pk = load_private_key(settings)
    if pk is None:
        return False
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None or not ruche.creator_instance_id:
        return False
    try:
        creator_pem = creator_member_public_key_pem(db, ruche_id)
        creator_pub = public_key_from_pem(creator_pem.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    release_sqlite_before_network(db)
    resp = fetch_member_list(
        base_url=peer_base_url, ruche_id=ruche_id, private_key=pk
    )
    if resp.status_code != 200:
        return False
    body = resp.json()
    payload = body.get("payload")
    sig = body.get("signature_b64")
    if not isinstance(payload, dict) or not isinstance(sig, str):
        return False
    prev = latest_member_list_version(db, ruche_id)
    try:
        row = apply_member_list_as_peer(
            db,
            ruche_id=ruche_id,
            creator_id=ruche.creator_instance_id,
            creator_public_key=creator_pub,
            payload=payload,
            signature_b64=sig,
            now=now,
            settings=settings,
        )
    except MemberListError:
        return False
    if row is None:
        return False
    members = payload.get("members")
    if not isinstance(members, list):
        return False
    member_ids = {str(m.get("id")) for m in members if isinstance(m.get("id"), str)}
    purge_entries_for_removed_members(db, ruche_id=ruche_id, member_ids=member_ids, now=now)
    return int(payload.get("version") or 0) > prev


def _entries_outbound(db: Session, *, ruche_id: bytes, peer_vector: dict[str, int]) -> list[dict[str, Any]]:
    from mediatracker.hive.peer_gossip import entries_page_for_peer

    page = entries_page_for_peer(db, ruche_id=ruche_id, peer_vector=peer_vector, limit=GOSSIP_PAGE_DEFAULT)
    return page.entries


def _signals_outbound(db: Session, *, ruche_id: bytes, peer_vector: dict[str, int]) -> list[dict[str, Any]]:
    page = signals_page_for_peer(db, ruche_id=ruche_id, peer_vector=peer_vector, limit=GOSSIP_PAGE_DEFAULT)
    return page.entries


def _record_peer_vectors_from_body(
    db: Session,
    *,
    ruche_id: bytes,
    peer_instance_id: str,
    body: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, int], dict[str, int]]:
    return record_peer_gossip_vectors(
        db,
        ruche_id=ruche_id,
        peer_instance_id=peer_instance_id,
        vector=body.get("vector"),
        signal_vector=body.get("signal_vector"),
        now=now,
    )


def send_gossip_hint_to_peer(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    peer_base_url: str,
    now: datetime,
    peer_instance_id: str | None = None,
) -> bool:
    """Signal « j'ai du nouveau » : vecteur + lot local contigu, sans tirage complet."""
    pk = load_private_key(settings)
    if pk is None:
        return False
    vector = version_vector_for_ruche(db, ruche_id)
    signal_vector = signal_version_vector_for_ruche(db, ruche_id)
    from mediatracker.hive.peer_gossip import entries_for_gossip_hint, signals_for_gossip_hint

    local_origin = hive_public_id_from_public_key(pk.public_key())
    known_vector, known_signals = (
        load_peer_gossip_vectors(db, ruche_id=ruche_id, peer_instance_id=peer_instance_id)
        if peer_instance_id
        else ({}, {})
    )
    outbound = entries_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=local_origin, peer_vector=known_vector
    )
    sig_out = signals_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=local_origin, peer_vector=known_signals
    )
    load = build_local_load_snapshot(db, now)
    instance = build_local_instance_snapshot(now)
    release_sqlite_before_network(db)
    resp = gossip_sync_once(
        base_url=peer_base_url,
        ruche_id=ruche_id,
        private_key=pk,
        vector=vector,
        entries=outbound,
        hint=True,
        signal_vector=signal_vector,
        signals=sig_out,
        load=load,
        instance=instance,
    )
    if resp.status_code != 200:
        return False
    if not peer_instance_id:
        return True
    try:
        body = resp.json()
        ingest_peer_load(
            db,
            ruche_id=ruche_id,
            origin_instance_id=peer_instance_id,
            load=body.get("load"),
            now=now,
        )
        if body.get("instance") is not None:
            ingest_peer_info(
                db,
                ruche_id=ruche_id,
                origin_instance_id=peer_instance_id,
                instance=body.get("instance"),
                now=now,
            )
        recorded_vector, recorded_signals = _record_peer_vectors_from_body(
            db,
            ruche_id=ruche_id,
            peer_instance_id=peer_instance_id,
            body=body if isinstance(body, dict) else {},
            now=now,
        )
    except Exception:
        return True
    if outbound or sig_out:
        return True
    # Premier lot vide (vecteur inconnu ou retard > 50) : si la réponse montre
    # un pair assez proche, envoyer le suffixe contigu, jamais un saut.
    follow_entries = entries_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=local_origin, peer_vector=recorded_vector
    )
    follow_signals = signals_for_gossip_hint(
        db, ruche_id=ruche_id, local_origin_id=local_origin, peer_vector=recorded_signals
    )
    if not follow_entries and not follow_signals:
        return True
    release_sqlite_before_network(db)
    try:
        follow = gossip_sync_once(
            base_url=peer_base_url,
            ruche_id=ruche_id,
            private_key=pk,
            vector=vector,
            entries=follow_entries,
            hint=True,
            signal_vector=signal_vector,
            signals=follow_signals,
            load=load,
            instance=instance,
        )
        if follow.status_code == 200:
            follow_body = follow.json()
            if isinstance(follow_body, dict):
                _record_peer_vectors_from_body(
                    db,
                    ruche_id=ruche_id,
                    peer_instance_id=peer_instance_id,
                    body=follow_body,
                    now=now,
                )
    except Exception:
        logger.exception("Second lot hint Hive échoué (ruche %s).", ruche_id.hex())
    return True


def pull_gossip_from_peer(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    peer_base_url: str,
    now: datetime,
    peer_instance_id: str | None = None,
    max_rounds: int = 64,
) -> int:
    pk = load_private_key(settings)
    if pk is None:
        return 0
    total = 0
    vector = version_vector_for_ruche(db, ruche_id)
    signal_vector = signal_version_vector_for_ruche(db, ruche_id)
    cursor: GossipCursor | None = None
    signal_cursor: GossipCursor | None = None
    collected_disponible_cles: list[dict] = []
    for _ in range(max_rounds):
        outbound = _entries_outbound(db, ruche_id=ruche_id, peer_vector=vector)
        sig_out = _signals_outbound(db, ruche_id=ruche_id, peer_vector=signal_vector)
        outbound_load = build_local_load_snapshot(db, now)
        outbound_instance = build_local_instance_snapshot(now)
        release_sqlite_before_network(db)
        resp = gossip_sync_once(
            base_url=peer_base_url,
            ruche_id=ruche_id,
            private_key=pk,
            vector=vector,
            entries=outbound,
            cursor=cursor.as_dict() if cursor else None,
            signal_vector=signal_vector,
            signals=sig_out,
            signal_cursor=signal_cursor.as_dict() if signal_cursor else None,
            load=outbound_load,
            instance=outbound_instance,
        )
        if resp.status_code != 200:
            break
        body = resp.json()
        if peer_instance_id:
            record_reachability(db, ruche_id=ruche_id, origin_id=peer_instance_id, now=now)
        if peer_instance_id and body.get("load") is not None:
            ingest_peer_load(
                db,
                ruche_id=ruche_id,
                origin_instance_id=peer_instance_id,
                load=body.get("load"),
                now=now,
            )
        if peer_instance_id and body.get("instance") is not None:
            ingest_peer_info(
                db,
                ruche_id=ruche_id,
                origin_instance_id=peer_instance_id,
                instance=body.get("instance"),
                now=now,
            )
        items = body.get("entries")
        if isinstance(items, list):
            added = ingest_signed_entries(
                db,
                ruche_id=ruche_id,
                items=items,
                now=now,
                settings=settings,
                collected_disponible_cles=collected_disponible_cles,
            )
            total += added
            if added > 0:
                from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

                mark_hive_catalog_sync_needed(db)
                release_sqlite_before_network(db)
        sig_items = body.get("signals")
        if isinstance(sig_items, list):
            sig_added = ingest_signed_signals(db, ruche_id=ruche_id, items=sig_items, now=now)
            total += sig_added
            if sig_added > 0:
                from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed
                from mediatracker.hive.signal_requests import try_auto_accept_incoming

                mark_hive_catalog_sync_needed(db)
                try_auto_accept_incoming(db, settings, ruche_id=ruche_id, now=now)
                release_sqlite_before_network(db)
        peer_vector = body.get("vector")
        if isinstance(peer_vector, dict):
            merged = dict(vector)
            for k, v in peer_vector.items():
                if isinstance(k, str) and isinstance(v, int):
                    merged[k] = max(merged.get(k, 0), v)
            vector = merged
            if peer_instance_id:
                record_peer_gossip_vectors(
                    db,
                    ruche_id=ruche_id,
                    peer_instance_id=peer_instance_id,
                    vector=peer_vector,
                    signal_vector=body.get("signal_vector"),
                    now=now,
                )
        peer_sig_vector = body.get("signal_vector")
        if isinstance(peer_sig_vector, dict):
            merged_sig = dict(signal_vector)
            for k, v in peer_sig_vector.items():
                if isinstance(k, str) and isinstance(v, int):
                    merged_sig[k] = max(merged_sig.get(k, 0), v)
            signal_vector = merged_sig
        has_more = body.get("has_more") is True
        sig_has_more = body.get("signal_has_more") is True
        if has_more:
            cursor = GossipCursor.from_dict(body.get("cursor"))
            if cursor is None:
                logger.warning("Gossip Hive : has_more sans curseur valide.")
                break
        else:
            cursor = None
        if sig_has_more:
            signal_cursor = GossipCursor.from_dict(body.get("signal_cursor"))
            if signal_cursor is None:
                logger.warning("Gossip Hive signaux : signal_has_more sans curseur valide.")
                break
        else:
            signal_cursor = None
        if not has_more and not sig_has_more:
            break
    if collected_disponible_cles:
        from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store
        from mediatracker.hive.source_download import try_enqueue_hive_for_ingested_offers

        refresh_hive_catalogue_store(db, now=now, settings=settings)
        try_enqueue_hive_for_ingested_offers(
            db,
            settings,
            ruche_id=ruche_id,
            cles=collected_disponible_cles,
            now=now,
        )
    return total
