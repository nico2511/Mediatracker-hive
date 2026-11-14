"""Choix de source (joignable, débit mesuré, aléa) pour un sha256 donné."""

from __future__ import annotations

import json
import random
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.hive.hive_catalogue import load_hive_catalogue_rows
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


def rank_sources(
    db: Session, sources: list[dict], *, rng: random.Random | None = None
) -> list[dict[str, Any]]:
    """Trie : meilleur débit mesuré, tirage à égalité (y compris débit inconnu = 0)."""
    if not sources:
        return []
    enriched = [_enrich_measured_bps(db, s) for s in sources if isinstance(s, dict)]
    r = rng or random.Random()
    by_tier: dict[float, list[dict]] = {}
    for s in enriched:
        by_tier.setdefault(_speed_key(s), []).append(s)
    speeds = sorted(by_tier.keys(), reverse=True)
    out: list[dict] = []
    for speed in speeds:
        bucket = list(by_tier[speed])
        r.shuffle(bucket)
        out.extend(bucket)
    return out


def sources_for_sha256(
    db: Session, *, sha256_hex: str, now: datetime, rng: random.Random | None = None
) -> list[dict[str, Any]]:
    """Sources joignables, toutes ruches confondues, triées par débit mesuré puis tirage."""
    del now
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
    return rank_sources(db, out, rng=rng)
