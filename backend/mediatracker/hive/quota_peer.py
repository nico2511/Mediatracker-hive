"""Quota hebdomadaire côté pair (lecture par le membre demandeur)."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting
from mediatracker.hive.member_list import current_members_as_records
from mediatracker.hive.membership import local_instance_identity
from mediatracker.hive.peer_client import signed_peer_request
from mediatracker.hive.peer_outbound import validate_hive_peer_base_url
from mediatracker.hive.transfer_policy import member_receive_quota_snapshot
from mediatracker.hive.transfer_settings import next_week_start_utc, quota_usage_status, week_start_date
from mediatracker.security.ssrf import OutboundUrlRejected

HIVE_QUOTA_RESET_NOTICES_KEY = "hive_quota_reset_notices"
MY_QUOTA_PEER_TIMEOUT_S = 2.5
_MY_QUOTA_MAX_WORKERS = 8
_QUOTA_NOTICE_MAX_AGE = timedelta(days=8)


def resolve_member_display_name(db: Session, *, ruche_id: bytes, instance_id: str) -> str:
    try:
        return member_display_name_from_records(current_members_as_records(db, ruche_id), instance_id)
    except Exception:
        return instance_id[:8]


def member_display_name_from_records(members: list[dict], instance_id: str) -> str:
    for m in members:
        if str(m.get("id")) != instance_id:
            continue
        doc = m.get("nom_doc") if isinstance(m.get("nom_doc"), dict) else {}
        nom = doc.get("nom") if isinstance(doc, dict) else None
        if isinstance(nom, str) and nom.strip():
            return nom.strip()
        return instance_id[:8]
    return instance_id[:8]


def _read_notices(db: Session) -> dict[str, dict[str, str]]:
    row = db.get(Setting, HIVE_QUOTA_RESET_NOTICES_KEY)
    if row is None or not row.value:
        return {}
    try:
        parsed = json.loads(str(row.value))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for ruche_hex, by_member in parsed.items():
        if not isinstance(ruche_hex, str) or not isinstance(by_member, dict):
            continue
        inner: dict[str, str] = {}
        for mid, iso in by_member.items():
            if isinstance(mid, str) and isinstance(iso, str) and iso.strip():
                inner[mid] = iso.strip()
        if inner:
            out[ruche_hex] = inner
    return out


def _write_notices(db: Session, data: dict[str, dict[str, str]]) -> None:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    row = db.get(Setting, HIVE_QUOTA_RESET_NOTICES_KEY)
    if row is None:
        db.add(Setting(key=HIVE_QUOTA_RESET_NOTICES_KEY, value=text))
    else:
        row.value = text
    db.flush()


def _parse_notice_iso(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _prune_stale_quota_notices(data: dict[str, dict[str, str]], *, now: datetime) -> dict[str, dict[str, str]]:
    cutoff = now.astimezone(timezone.utc) - _QUOTA_NOTICE_MAX_AGE
    out: dict[str, dict[str, str]] = {}
    for ruche_hex, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        kept: dict[str, str] = {}
        for mid, iso in bucket.items():
            if not isinstance(mid, str) or not isinstance(iso, str):
                continue
            moment = _parse_notice_iso(iso.strip())
            if moment is not None and moment >= cutoff:
                kept[mid] = iso.strip()
        if kept:
            out[ruche_hex] = kept
    return out


def peek_quota_reset_notice(
    db: Session,
    *,
    ruche_id: bytes,
    member_instance_id: str,
    now: datetime,
) -> str | None:
    """Avis de reset sans retrait (dédup côté membre ; expiration serveur)."""
    data = _prune_stale_quota_notices(_read_notices(db), now=now)
    bucket = data.get(ruche_id.hex())
    if not bucket:
        return None
    iso = bucket.get(member_instance_id)
    if not isinstance(iso, str) or not iso.strip():
        return None
    moment = _parse_notice_iso(iso.strip())
    if moment is None:
        return None
    if moment < now.astimezone(timezone.utc) - _QUOTA_NOTICE_MAX_AGE:
        return None
    return iso.strip()


def queue_quota_reset_notice(
    db: Session,
    *,
    ruche_id: bytes,
    member_instance_id: str,
    when: datetime,
) -> None:
    data = _prune_stale_quota_notices(_read_notices(db), now=when)
    ruche_hex = ruche_id.hex()
    bucket = dict(data.get(ruche_hex) or {})
    bucket[member_instance_id] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    data[ruche_hex] = bucket
    _write_notices(db, data)


def quota_for_requester_payload(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    requester_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Réponse limitée au demandeur authentifié (jamais un autre membre)."""
    try:
        _, local_id, doc, *_rest = local_instance_identity(db, settings)
    except ValueError as exc:
        raise ValueError("no_identity") from exc
    nom = doc.get("nom") if isinstance(doc, dict) else None
    source_name = nom.strip() if isinstance(nom, str) and nom.strip() else local_id[:8]
    snap = member_receive_quota_snapshot(db, ruche_id=ruche_id, instance_id=requester_id, now=now)
    ws = week_start_date(now)
    reset_at = next_week_start_utc(now).isoformat().replace("+00:00", "Z")
    body: dict[str, Any] = {
        "source_instance_id": local_id,
        "source_display_name": source_name,
        "shares_in_ruche": bool(snap["shares_in_ruche"]),
        "bytes_used": snap["receive_bytes_used"],
        "quota_bytes": snap["receive_quota_bytes"],
        "week_start": ws.isoformat(),
        "reset_at": reset_at,
    }
    notice_at = peek_quota_reset_notice(
        db, ruche_id=ruche_id, member_instance_id=requester_id, now=now
    )
    if notice_at:
        body["owner_reset_at"] = notice_at
    return body


def _member_display(mid: str, records: list[dict]) -> str:
    return member_display_name_from_records(records, mid)


def _peer_sources(db: Session, *, ruche_id: bytes, instance_id: str) -> list[tuple[str, str, str]]:
    records = current_members_as_records(db, ruche_id)
    out: list[tuple[str, str, str]] = []
    for m in records:
        mid = str(m.get("id") or "")
        if not mid or mid == instance_id:
            continue
        addr = str(m.get("address") or "").strip()
        if not addr or addr == "tirage":
            continue
        try:
            base = validate_hive_peer_base_url(addr)
        except OutboundUrlRejected:
            continue
        name = _member_display(mid, records)
        out.append((mid, name, base))
    return out


def _fetch_one_quota(
    *,
    base_url: str,
    ruche_id: bytes,
    private_key,
    source_instance_id: str,
    source_display_name: str,
    when: datetime,
) -> dict[str, Any]:
    path = f"/hive/ruche/{ruche_id.hex()}/quota-for-peer"
    try:
        resp = signed_peer_request(
            base_url=base_url,
            method="GET",
            path=path,
            ruche_id=ruche_id,
            private_key=private_key,
            body=b"",
            when=when,
            timeout=MY_QUOTA_PEER_TIMEOUT_S,
        )
    except Exception:
        return {
            "status": "unavailable",
            "source_instance_id": source_instance_id,
            "source_display_name": source_display_name,
        }
    if resp.status_code != 200:
        return {
            "status": "unavailable",
            "source_instance_id": source_instance_id,
            "source_display_name": source_display_name,
        }
    try:
        data = resp.json()
    except ValueError:
        return {
            "status": "unavailable",
            "source_instance_id": source_instance_id,
            "source_display_name": source_display_name,
        }
    if not isinstance(data, dict):
        return {
            "status": "unavailable",
            "source_instance_id": source_instance_id,
            "source_display_name": source_display_name,
        }
    used = data.get("bytes_used")
    limit = data.get("quota_bytes")
    status = "ok"
    quota_status = None
    if data.get("shares_in_ruche") is True:
        status = "no_quota"
    elif isinstance(used, int) and isinstance(limit, int):
        quota_status = quota_usage_status(used, limit)
    return {
        "status": status,
        "source_instance_id": source_instance_id,
        "source_display_name": source_display_name,
        "shares_in_ruche": data.get("shares_in_ruche"),
        "bytes_used": used,
        "quota_bytes": limit,
        "week_start": data.get("week_start"),
        "reset_at": data.get("reset_at"),
        "receive_quota_status": quota_status,
        "owner_reset_at": data.get("owner_reset_at"),
    }


def aggregate_my_quota(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    now: datetime,
) -> dict[str, Any]:
    try:
        pk, instance_id, *_rest = local_instance_identity(db, settings)
    except ValueError as exc:
        raise ValueError("no_identity") from exc
    sources = _peer_sources(db, ruche_id=ruche_id, instance_id=instance_id)
    ws = week_start_date(now)
    reset_at = next_week_start_utc(now).isoformat().replace("+00:00", "Z")
    cards: list[dict[str, Any]] = []
    if not sources:
        return {
            "ruche_id_hex": ruche_id.hex(),
            "week_start": ws.isoformat(),
            "reset_at": reset_at,
            "sources": cards,
        }
    workers = min(_MY_QUOTA_MAX_WORKERS, max(1, len(sources)))
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {
        pool.submit(
            _fetch_one_quota,
            base_url=base,
            ruche_id=ruche_id,
            private_key=pk,
            source_instance_id=mid,
            source_display_name=name,
            when=now,
        ): (mid, name)
        for mid, name, base in sources
    }
    collected: set = set()
    try:
        try:
            for fut in as_completed(futures, timeout=MY_QUOTA_PEER_TIMEOUT_S):
                collected.add(fut)
                try:
                    cards.append(fut.result())
                except Exception:
                    mid, name = futures[fut]
                    cards.append(
                        {
                            "status": "unavailable",
                            "source_instance_id": mid,
                            "source_display_name": name,
                        }
                    )
        except TimeoutError:
            for fut, (mid, name) in futures.items():
                if fut in collected:
                    continue
                if fut.done():
                    try:
                        cards.append(fut.result())
                    except Exception:
                        cards.append(
                            {
                                "status": "unavailable",
                                "source_instance_id": mid,
                                "source_display_name": name,
                            }
                        )
                else:
                    fut.cancel()
                    cards.append(
                        {
                            "status": "unavailable",
                            "source_instance_id": mid,
                            "source_display_name": name,
                        }
                    )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {
        "ruche_id_hex": ruche_id.hex(),
        "week_start": ws.isoformat(),
        "reset_at": reset_at,
        "sources": cards,
    }
