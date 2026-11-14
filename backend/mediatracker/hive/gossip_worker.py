"""Gossip Hive périodique (worker) : tirage sortant, listes membres, catalogue."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.db.models import HiveMember, Setting
from mediatracker.db.session import commit_retry, is_sqlite_locked
from mediatracker.hive.crypto_id import hive_public_id_from_public_key
from mediatracker.hive.gossip_exchange import (
    pull_gossip_from_peer,
    pull_member_list_from_peer,
)
from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_enabled_in_db, load_private_key
from mediatracker.hive.membership import ruches_for_instance

logger = logging.getLogger(__name__)

HIVE_GOSSIP_NEXT_AT_KEY = "hive_gossip_next_at"
_GOSSIP_BASE_SECONDS = 600.0
_GOSSIP_JITTER_SECONDS = 120.0


def _read_next_at(db: Session) -> datetime | None:
    row = db.get(Setting, HIVE_GOSSIP_NEXT_AT_KEY)
    if row is None or not (row.value or "").strip():
        return None
    try:
        return datetime.fromisoformat(row.value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _write_next_at(db: Session, when: datetime) -> None:
    iso = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    row = db.get(Setting, HIVE_GOSSIP_NEXT_AT_KEY)
    if row is None:
        db.add(Setting(key=HIVE_GOSSIP_NEXT_AT_KEY, value=iso))
    else:
        row.value = iso


def schedule_next_gossip(db: Session, now: datetime, *, interval_seconds: float | None = None) -> datetime:
    if interval_seconds is None:
        interval_seconds = _GOSSIP_BASE_SECONDS + random.uniform(
            -_GOSSIP_JITTER_SECONDS, _GOSSIP_JITTER_SECONDS
        )
    nxt = now + timedelta(seconds=max(60.0, interval_seconds))
    _write_next_at(db, nxt)
    return nxt


def hive_gossip_due(db: Session, now: datetime) -> bool:
    if not hive_enabled_in_db(db):
        return False
    nxt = _read_next_at(db)
    if nxt is None:
        return True
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    a = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return a >= nxt.astimezone(timezone.utc)


def _is_public_peer(address: str) -> bool:
    text = address.strip()
    return bool(text) and text != "tirage"


def public_peer_targets(
    db: Session,
    *,
    ruche_id: bytes,
    self_instance_id: str,
    count: int = 2,
    rng: random.Random | None = None,
) -> list[tuple[str, str]]:
    """(member_instance_id, base_url) : au plus ``count`` pairs publics tirés au hasard (cadrage)."""
    rows = list(db.scalars(select(HiveMember).where(HiveMember.ruche_id == ruche_id)).all())
    candidates: list[tuple[str, str]] = []
    for row in rows:
        if row.member_instance_id == self_instance_id:
            continue
        if not _is_public_peer(row.public_address):
            continue
        candidates.append((row.member_instance_id, row.public_address.strip().rstrip("/")))
    if not candidates:
        return []
    pick = min(count, len(candidates))
    source = rng or random
    return source.sample(candidates, pick)


@dataclass(frozen=True)
class PeerExchangeTarget:
    ruche_id: bytes
    peer_base_url: str
    peer_instance_id: str | None = None


def run_peer_exchange(db: Session, settings: Settings, target: PeerExchangeTarget, *, now: datetime) -> None:
    pull_member_list_from_peer(
        db,
        settings,
        ruche_id=target.ruche_id,
        peer_base_url=target.peer_base_url,
        now=now,
    )
    pull_gossip_from_peer(
        db,
        settings,
        ruche_id=target.ruche_id,
        peer_base_url=target.peer_base_url,
        now=now,
    )


def hive_gossip_tick(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    force: bool = False,
    peer_targets: list[PeerExchangeTarget] | None = None,
) -> bool:
    """Un passage gossip léger. Retourne True si du travail a été tenté."""
    moment = now or datetime.now(timezone.utc)
    if not hive_enabled_in_db(db):
        return False
    if not force and not hive_gossip_due(db, moment):
        return False
    pk = load_private_key(settings)
    if pk is None:
        schedule_next_gossip(db, moment)
        return False
    instance_id = hive_public_id_from_public_key(pk.public_key())

    if peer_targets is not None:
        for target in peer_targets:
            try:
                run_peer_exchange(db, settings, target, now=moment)
            except Exception:
                logger.exception("Échange gossip Hive échoué (ruche %s).", target.ruche_id.hex())
        refresh_hive_catalogue_store(db, now=moment, settings=settings)
        if not force:
            schedule_next_gossip(db, moment)
        return True

    for ruche in ruches_for_instance(db, instance_id):
        rid = ruche.ruche_id
        peers = public_peer_targets(db, ruche_id=rid, self_instance_id=instance_id)
        if not peers:
            continue
        for _, base_url in peers:
            try:
                run_peer_exchange(
                    db,
                    settings,
                    PeerExchangeTarget(ruche_id=rid, peer_base_url=base_url),
                    now=moment,
                )
            except Exception:
                logger.exception("Gossip Hive vers pair échoué (ruche %s).", rid.hex())

    refresh_hive_catalogue_store(db, now=moment, settings=settings)
    schedule_next_gossip(db, moment)
    return True


def _isolated_gossip_tick(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    moment: datetime,
    force: bool = False,
    peer_targets: list[PeerExchangeTarget] | None = None,
) -> None:
    db = factory()
    try:
        hive_gossip_tick(
            db,
            settings,
            now=moment,
            force=force,
            peer_targets=peer_targets,
        )
        commit_retry(db)
    finally:
        db.close()


def _isolated_gossip_burst(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    moment: datetime,
) -> None:
    from mediatracker.hive.gossip_burst import hive_gossip_burst_tick

    burst_db = factory()
    try:
        hive_gossip_burst_tick(burst_db, settings, now=moment)
        commit_retry(burst_db)
    finally:
        burst_db.close()


def worker_hive_gossip_pass(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    now: datetime | None = None,
    force: bool = False,
    peer_targets: list[PeerExchangeTarget] | None = None,
) -> None:
    """Passage worker : sessions isolées, commit avant phases réseau (hors transaction parente)."""
    moment = now or datetime.now(timezone.utc)
    _isolated_gossip_tick(
        factory, settings, moment=moment, force=force, peer_targets=peer_targets
    )
    _isolated_gossip_burst(factory, settings, moment=moment)


def run_worker_hive_gossip_isolated(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> None:
    """Entrée worker : gossip et burst isolés ; le burst continue même si le tick échoue."""
    moment = now or datetime.now(timezone.utc)
    try:
        _isolated_gossip_tick(factory, settings, moment=moment)
    except OperationalError as exc:
        if is_sqlite_locked(exc):
            logger.warning("Gossip Hive : base occupée, nouvel essai au prochain passage.")
        else:
            logger.exception("Gossip Hive a échoué.")
    except Exception:
        logger.exception("Gossip Hive a échoué.")
    try:
        _isolated_gossip_burst(factory, settings, moment=moment)
    except OperationalError as exc:
        if is_sqlite_locked(exc):
            logger.warning("Signal gossip Hive : base occupée, nouvel essai au prochain passage.")
        else:
            logger.exception("Signal gossip Hive a échoué.")
    except Exception:
        logger.exception("Signal gossip Hive a échoué.")
