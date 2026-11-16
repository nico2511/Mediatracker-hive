"""Choix de source (joignable, débit mesuré, aléa) pour un sha256 donné."""

from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.hive.hive_catalogue import load_hive_catalogue_rows
from mediatracker.hive.transfer_load import peer_load_row, ranking_tier
from mediatracker.hive.transfer_throughput import measured_bps_for_origin


def _speed_key(source: dict) -> float:
    raw = source.get("measured_bps")
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return float(raw)
    return 0.0


def _enrich_measured_bps(db: Session, source: dict) -> dict:
    out = dict(source)
    try:
        rid = bytes.fromhex(str(out["ruche_id_hex"]))
    except (ValueError, TypeError):
        return out
    origin = str(out.get("origine") or "")
    if not origin:
        return out
    bps = measured_bps_for_origin(db, ruche_id=rid, origin_instance_id=origin)
    if bps is not None:
        out["measured_bps"] = bps
    return out


def _rank_key(
    db: Session, source: dict, *, now: datetime, rng: random.Random
) -> tuple[int, float, float, float]:
    try:
        rid = bytes.fromhex(str(source.get("ruche_id_hex") or ""))
    except (ValueError, TypeError):
        rid = b""
    origin = str(source.get("origine") or "")
    load = peer_load_row(db, ruche_id=rid, origin_instance_id=origin, now=now) if rid and origin else None
    tier = ranking_tier(load)
    free_ratio = load.free_capacity_ratio() if load is not None and tier == 0 else 0.0
    speed = _speed_key(source)
    return (tier, -free_ratio, -speed, rng.random())


def rank_sources(
    db: Session,
    sources: list[dict],
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Trie : libre (capacité), neutre, occupé ; puis débit mesuré ; tirage à égalité."""
    if not sources:
        return []
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        from datetime import timezone

        moment = moment.replace(tzinfo=timezone.utc)
    enriched = [_enrich_measured_bps(db, s) for s in sources if isinstance(s, dict)]
    r = rng or random.Random()
    decorated: list[tuple[tuple[int, float, float, float], dict]] = [
        (_rank_key(db, s, now=moment, rng=r), s) for s in enriched
    ]
    decorated.sort(key=lambda pair: pair[0])
    return [s for _, s in decorated]


def sources_for_sha256(
    db: Session, *, sha256_hex: str, now: datetime, rng: random.Random | None = None
) -> list[dict[str, Any]]:
    """Sources joignables, toutes ruches confondues, triées par charge puis débit mesuré."""
    out: list[dict] = []
    for row in load_hive_catalogue_rows(db):
        try:
            sources = json.loads(row.sources_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(sources, list):
            continue
        for src in sources:
            if not isinstance(src, dict):
                continue
            if src.get("sha256") != sha256_hex:
                continue
            if not src.get("joignable"):
                continue
            if not src.get("file_id_hex") or not src.get("ruche_id_hex"):
                continue
            out.append(dict(src))
    return rank_sources(db, out, now=now, rng=rng)
