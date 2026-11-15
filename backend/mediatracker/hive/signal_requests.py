"""Demandes anonymes : rapprochement local, accepter/refuser, vues API."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.errors import Problem
from mediatracker.config import Settings
from mediatracker.db.models import Download, HiveRequestResolution, HiveSignal, Media, MediaItem
from mediatracker.hive.crypto_id import hive_public_id_from_public_key
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.membership import ruches_for_instance
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.hive.signal import (
    cle_hash,
    item_file_present,
    latest_cherche_rows_for_ruche,
    parse_cle,
)
from mediatracker.hive.signal_cherche_retract import cherche_retract_active
from mediatracker.hive.signal_miss_prompt import cherche_miss_prompt_active
from mediatracker.hive.signal_policy import (
    cherche_slot_eligible_for_publish,
    hive_auto_accept_requests_enabled,
)


def opaque_request_id(*, ruche_id: bytes, requester_origin: str, cle: dict) -> str:
    h = hashlib.sha256()
    h.update(ruche_id)
    h.update(requester_origin.encode("utf-8"))
    h.update(cle_hash(cle).encode("ascii"))
    return h.hexdigest()[:24]


def _self_origin(settings: Settings) -> str | None:
    pk = load_private_key(settings)
    if pk is None:
        return None
    return hive_public_id_from_public_key(pk.public_key())


def _resolution_row(
    db: Session, *, ruche_id: bytes, requester_origin: str, cle: dict
) -> HiveRequestResolution | None:
    return db.scalar(
        select(HiveRequestResolution)
        .where(
            HiveRequestResolution.ruche_id == ruche_id,
            HiveRequestResolution.requester_origin == requester_origin,
            HiveRequestResolution.cle_hash == cle_hash(cle),
        )
        .limit(1)
    )


def _find_local_item_for_cle(db: Session, cle: dict) -> tuple[Media, MediaItem] | None:
    tmdb_id = cle.get("tmdb_id")
    media_type = str(cle.get("type") or "film")
    if tmdb_id is None:
        return None
    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is None:
        return None
    if media_type == "series":
        season, episode = cle.get("saison"), cle.get("episode")
        if season is None or episode is None:
            return None
        item = db.scalar(
            select(MediaItem).where(
                MediaItem.media_id == media.id,
                MediaItem.season == season,
                MediaItem.episode == episode,
            ).limit(1)
        )
    else:
        item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
    if item is None or not item_file_present(db, item):
        return None
    return media, item


def _active_remote_cherche(
    db: Session, *, ruche_id: bytes, self_origin: str | None
) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for row in latest_cherche_rows_for_ruche(db, ruche_id=ruche_id):
        origin = row.origin_instance_id
        if not origin or (self_origin and origin == self_origin):
            continue
        cle = parse_cle(row.cle_json)
        if cle:
            out.append((origin, cle))
    return out


def local_cherche_active(db: Session, *, ruche_id: bytes, origin: str, cle: dict) -> bool:
    target = cle_hash(cle)
    row = db.scalar(
        select(HiveSignal)
        .where(
            HiveSignal.ruche_id == ruche_id,
            HiveSignal.origin_instance_id == origin,
            HiveSignal.cle_hash == target,
        )
        .limit(1)
    )
    return row is not None and row.signal == "cherche"


def list_received_requests(db: Session, settings: Settings) -> list[dict[str, Any]]:
    self_origin = _self_origin(settings)
    out: list[dict[str, Any]] = []
    for ruche in ruches_for_instance(db, self_origin or ""):
        rid = ruche.ruche_id
        for origin, cle in _active_remote_cherche(db, ruche_id=rid, self_origin=self_origin):
            resolution = _resolution_row(db, ruche_id=rid, requester_origin=origin, cle=cle)
            if resolution is not None:
                continue
            matched = _find_local_item_for_cle(db, cle)
            if matched is None:
                continue
            media, item = matched
            from mediatracker.hive.share_policy import effective_exclude_all_for_ui, has_share_intent

            assert item.id is not None
            already_shared = has_share_intent(db, item.id) and not effective_exclude_all_for_ui(db, item.id)
            out.append(
                {
                    "request_id": opaque_request_id(ruche_id=rid, requester_origin=origin, cle=cle),
                    "work_key": _stable_work_key(cle),
                    "title": media.title,
                    "type": media.type,
                    "tmdb_id": media.tmdb_id,
                    "season": item.season,
                    "episode": item.episode,
                    "media_item_id": item.id,
                    "already_shared": already_shared,
                    "status": "pending",
                }
            )
    return out


def _resolve_request_id(
    db: Session, settings: Settings, request_id: str
) -> tuple[bytes, str, dict, Media, MediaItem] | None:
    self_origin = _self_origin(settings)
    for ruche in ruches_for_instance(db, self_origin or ""):
        rid = ruche.ruche_id
        for origin, cle in _active_remote_cherche(db, ruche_id=rid, self_origin=self_origin):
            if opaque_request_id(ruche_id=rid, requester_origin=origin, cle=cle) != request_id:
                continue
            matched = _find_local_item_for_cle(db, cle)
            if matched is None:
                return None
            return rid, origin, cle, matched[0], matched[1]
    return None


def accept_received_request(db: Session, settings: Settings, *, request_id: str, now: datetime) -> None:
    resolved = _resolve_request_id(db, settings, request_id)
    if resolved is None:
        raise Problem(404, "Introuvable", "Demande introuvable ou déjà traitée.")
    rid, origin, cle, _media, item = resolved
    if item.id is None:
        raise Problem(422, "Requête invalide", "Fichier local indisponible.")
    from mediatracker.hive.catalog import sync_hive_catalog_item

    grant_share_intent(db, item.id)
    sync_hive_catalog_item(db, settings, item.id, now=now)
    row = _resolution_row(db, ruche_id=rid, requester_origin=origin, cle=cle)
    if row is None:
        db.add(
            HiveRequestResolution(
                ruche_id=rid,
                requester_origin=origin,
                cle_hash=cle_hash(cle),
                status="accepted",
                media_item_id=item.id,
                updated_at=now,
            )
        )
    else:
        row.status = "accepted"
        row.media_item_id = item.id
        row.updated_at = now


def refuse_received_request(db: Session, settings: Settings, *, request_id: str, now: datetime) -> None:
    resolved = _resolve_request_id(db, settings, request_id)
    if resolved is None:
        raise Problem(404, "Introuvable", "Demande introuvable ou déjà traitée.")
    rid, origin, cle, _media, item = resolved
    row = _resolution_row(db, ruche_id=rid, requester_origin=origin, cle=cle)
    if row is None:
        db.add(
            HiveRequestResolution(
                ruche_id=rid,
                requester_origin=origin,
                cle_hash=cle_hash(cle),
                status="refused",
                media_item_id=item.id if item.id else None,
                updated_at=now,
            )
        )
    else:
        row.status = "refused"
        row.updated_at = now


def _item_in_transfer(db: Session, item: MediaItem) -> bool:
    if item.id is None:
        return False
    row = db.scalar(
        select(Download)
        .where(
            Download.media_item_id == item.id,
            Download.state.in_(("pending", "downloading", "paused", "verifying")),
        )
        .limit(1)
    )
    return row is not None


def list_my_requests(db: Session, settings: Settings) -> list[dict[str, Any]]:
    self_origin = _self_origin(settings)
    if self_origin is None:
        return []
    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    medias = list(db.scalars(select(Media).where(Media.followed.is_(True))).all())
    for media in medias:
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        for item in items:
            if item_file_present(db, item) or item.watched:
                continue
            if item.id is None:
                continue
            if not cherche_slot_eligible_for_publish(media, item, now=now):
                continue
            from mediatracker.hive.catalog import _cle_document

            cle = _cle_document(media, item)
            announced = any(
                local_cherche_active(db, ruche_id=r.ruche_id, origin=self_origin, cle=cle)
                for r in ruches_for_instance(db, self_origin)
            )
            prompt = cherche_miss_prompt_active(db, item.id)
            retracted = cherche_retract_active(db, item.id)
            if not announced and not prompt and not retracted:
                continue
            entry: dict[str, Any] = {
                "media_id": media.id,
                "media_item_id": item.id,
                "work_key": _stable_work_key(cle),
                "title": media.title,
                "type": media.type,
                "tmdb_id": media.tmdb_id,
                "season": item.season,
                "episode": item.episode,
            }
            if announced:
                entry["status"] = "transfer" if _item_in_transfer(db, item) else "pending"
                entry["hive_request_available"] = False
            elif retracted:
                entry["status"] = "retracted"
                entry["hive_request_available"] = True
            else:
                entry["status"] = "prompt"
                entry["hive_request_available"] = prompt and not announced
            out.append(entry)
    return out


def try_auto_accept_incoming(db: Session, settings: Settings, *, ruche_id: bytes, now: datetime) -> int:
    if not hive_auto_accept_requests_enabled(db):
        return 0
    self_origin = _self_origin(settings)
    accepted = 0
    for origin, cle in _active_remote_cherche(db, ruche_id=ruche_id, self_origin=self_origin):
        if _resolution_row(db, ruche_id=ruche_id, requester_origin=origin, cle=cle) is not None:
            continue
        if _find_local_item_for_cle(db, cle) is None:
            continue
        rid = opaque_request_id(ruche_id=ruche_id, requester_origin=origin, cle=cle)
        accept_received_request(db, settings, request_id=rid, now=now)
        accepted += 1
    return accepted