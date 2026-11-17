"""Vue locale fusionnée hive_catalogue (table SQLite, jamais servie sous /hive/)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveCatalogue, HiveEntry, HiveMember
from mediatracker.hive.catalog_validity import (
    announced_size_for_entry,
    remote_catalog_offer_plausible,
    version_parts_from_cle,
)
from mediatracker.hive.crypto_id import hive_public_id_from_public_key
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_gossip import origin_is_reachable
from mediatracker.hive.source_offer_mask import masked_source_keys, purge_masks_superseded_by_tombstones
from mediatracker.hive.transfer_throughput import measured_bps_for_origin


def _local_instance_id(settings: Settings | None) -> str | None:
    if settings is None:
        return None
    pk = load_private_key(settings)
    if pk is None:
        return None
    return hive_public_id_from_public_key(pk.public_key())


def _work_key(cle: dict) -> tuple:
    base = (cle.get("type"), cle.get("tmdb_id"))
    if cle.get("type") == "series":
        return base + (cle.get("saison"), cle.get("episode"))
    return base


def _version_key(cle: dict) -> str:
    return str(cle.get("version") or "")


def _stable_work_key(cle: dict) -> str:
    raw = json.dumps(_work_key(cle), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _offer_dedup_key(row: HiveEntry) -> tuple[bytes, str, bytes | str] | None:
    if row.origin_instance_id is None or row.ruche_id is None:
        return None
    if row.file_id is not None:
        return (row.ruche_id, row.origin_instance_id, row.file_id)
    if row.cle_json:
        return (row.ruche_id, row.origin_instance_id, row.cle_json)
    return None


def _latest_entry_per_offer(db: Session) -> dict[tuple[bytes, str, bytes | str], HiveEntry]:
    """Dernière entrée signée par offre (ruche, origine, file_id) ou cle_json si file_id absent."""
    rows = list(db.scalars(select(HiveEntry)).all())
    best: dict[tuple[bytes, str, bytes | str], HiveEntry] = {}
    for row in rows:
        key = _offer_dedup_key(row)
        if key is None:
            continue
        prev = best.get(key)
        if prev is None or int(row.seq) > int(prev.seq):
            best[key] = row
    return best


def build_merged_hive_catalogue(
    db: Session, *, now: datetime, settings: Settings | None = None
) -> list[dict[str, Any]]:
    """Calcule la vue fusionnée (agrégation en mémoire)."""
    local_id = _local_instance_id(settings)
    purge_masks_superseded_by_tombstones(db)
    masked = masked_source_keys(db)
    latest_per_offer = _latest_entry_per_offer(db)
    rows = [row for row in latest_per_offer.values() if row.etat == "disponible"]
    by_work: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        origin = row.origin_instance_id
        assert origin is not None and row.ruche_id is not None
        try:
            cle = json.loads(row.cle_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(cle, dict):
            continue
        payload: dict[str, Any] = {}
        if row.payload_json:
            try:
                parsed = json.loads(row.payload_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                pass
        fiche: dict[str, Any] = {}
        if row.fiche_json:
            try:
                parsed = json.loads(row.fiche_json)
                if isinstance(parsed, dict):
                    fiche = parsed
            except json.JSONDecodeError:
                pass
        if not remote_catalog_offer_plausible(
            cle=cle,
            fiche=fiche,
            payload=payload,
            sha256_hex=row.sha256_hex,
            etat=row.etat,
        ):
            continue
        wk = _work_key(cle)
        vk = _version_key(cle)
        bucket = by_work.setdefault(wk, {"cle": cle, "versions": {}})
        versions: dict[str, dict] = bucket["versions"]
        ver = versions.setdefault(vk, {"version": vk, "sources": []})
        addr = db.scalar(
            select(HiveMember.public_address)
            .where(HiveMember.ruche_id == row.ruche_id, HiveMember.member_instance_id == origin)
            .limit(1)
        )
        if row.file_id is not None and (row.ruche_id, origin, row.file_id) in masked:
            continue
        if local_id is not None and origin == local_id:
            joignable = True
        else:
            joignable = origin_is_reachable(db, ruche_id=row.ruche_id, origin_id=origin, now=now)
        if addr is not None and (not str(addr).strip() or str(addr).strip() == "tirage"):
            joignable = False
        announced = announced_size_for_entry(row)
        q, lang = version_parts_from_cle(cle)
        if q == "unknown" and lang == "unknown" and announced is not None and announced > 0:
            fiche = dict(fiche)
            fiche.setdefault("taille", announced)
        src: dict[str, Any] = {
            "ruche_id_hex": row.ruche_id.hex(),
            "origine": origin,
            "file_id_hex": row.file_id.hex() if row.file_id else None,
            "sha256": row.sha256_hex,
            "joignable": joignable,
        }
        nom = fiche.get("nom_fichier")
        if isinstance(nom, str) and nom.strip():
            src["nom_fichier"] = nom.strip()
        bps = measured_bps_for_origin(db, ruche_id=row.ruche_id, origin_instance_id=origin)
        if bps is not None:
            src["measured_bps"] = bps
        ver["sources"].append(src)
    out: list[dict[str, Any]] = []
    for bucket in by_work.values():
        versions_list = list(bucket["versions"].values())
        for v in versions_list:
            v["disponible"] = any(s.get("joignable") for s in v["sources"])
        out.append({"cle": bucket["cle"], "versions": versions_list})
    return out


def refresh_hive_catalogue_store(db: Session, *, now: datetime, settings: Settings | None = None) -> int:
    """Recalcule et persiste la table hive_catalogue."""
    merged = build_merged_hive_catalogue(db, now=now, settings=settings)
    db.execute(delete(HiveCatalogue))
    count = 0
    for work in merged:
        cle = work.get("cle")
        if not isinstance(cle, dict):
            continue
        wk = _stable_work_key(cle)
        for ver in work.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            vk = str(ver.get("version") or "")
            sources = ver.get("sources")
            if not isinstance(sources, list):
                sources = []
            db.add(
                HiveCatalogue(
                    work_key=wk,
                    version_key=vk,
                    cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    sources_json=json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    disponible=bool(ver.get("disponible")),
                    refreshed_at=now,
                )
            )
            count += 1
    return count


def load_hive_catalogue_rows(db: Session) -> list[HiveCatalogue]:
    return list(db.scalars(select(HiveCatalogue).order_by(HiveCatalogue.work_key, HiveCatalogue.version_key)).all())
