"""Instantané version / protocole pair : gossip, stockage, lecture."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.core.build_info import app_version, fingerprint_suffix
from mediatracker.db.models import HivePeerInfo
from mediatracker.hive.access import is_active_member
from mediatracker.hive.transfer_load import LOAD_MAX_FUTURE_SECONDS

HIVE_PROTOCOL = 1
KNOWN_HIVE_CAPABILITIES: frozenset[str] = frozenset({"catalog_epoch"})

_APP_VERSION_MAX = 64
_CAP_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_FORBIDDEN_SUBSTRINGS = (
    "://",
    "\\\\",
    "/etc/",
    "/var/",
    "MEDIATRACKER_",
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "PRIVATE_KEY",
)


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_snapshot_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _aware_utc(parsed)


def _display_version_string() -> str:
    version = app_version()
    suffix = fingerprint_suffix()
    if suffix:
        return f"{version}@{suffix}"
    return version


def _clamp_protocol(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 65535:
        return None
    return value


def _validate_version_text(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or len(text) > _APP_VERSION_MAX:
        return None
    if not all(ch.isprintable() and ch not in "\r\n\t" for ch in text):
        return None
    lower = text.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle.lower() in lower:
            return None
    return text


def _normalize_capabilities(raw: object) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    if len(raw) > 32:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            return None
        if not _CAP_RE.fullmatch(item):
            return None
        if item not in KNOWN_HIVE_CAPABILITIES:
            continue
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _leaks_secrets(raw: dict[str, Any]) -> bool:
    try:
        blob = json.dumps(raw, ensure_ascii=True).lower()
    except (TypeError, ValueError):
        return True
    if "://" in blob:
        return True
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle.lower() in blob:
            return True
    return False


def validate_instance_payload(raw: object) -> dict[str, Any] | None:
    """Valide un objet ``instance`` gossip ; retourne un dict normalisé ou None."""
    if not isinstance(raw, dict):
        return None
    if _leaks_secrets(raw):
        return None
    version = _validate_version_text(raw.get("version"))
    protocol = _clamp_protocol(raw.get("hive_protocol"))
    if version is None or protocol is None:
        return None
    caps = _normalize_capabilities(raw.get("capabilities"))
    if caps is None:
        return None
    snapshot_at = _parse_snapshot_at(raw.get("at"))
    if snapshot_at is None:
        return None
    return {
        "version": version,
        "hive_protocol": protocol,
        "capabilities": caps,
        "at": snapshot_at,
    }


def build_local_instance_snapshot(now: datetime) -> dict[str, Any]:
    """Instantané publiable en gossip (toujours émis, y compris sans gate transfert)."""
    moment = _aware_utc(now)
    return {
        "version": _display_version_string(),
        "hive_protocol": HIVE_PROTOCOL,
        "capabilities": sorted(KNOWN_HIVE_CAPABILITIES),
        "at": moment.isoformat().replace("+00:00", "Z"),
    }


def ingest_peer_info(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    instance: object,
    now: datetime,
) -> bool:
    normalized = validate_instance_payload(instance)
    if normalized is None:
        return False
    moment = _aware_utc(now)
    snapshot_at = _aware_utc(normalized["at"])
    if (snapshot_at - moment).total_seconds() > LOAD_MAX_FUTURE_SECONDS:
        return False
    key = (ruche_id, origin_instance_id)
    row = db.get(HivePeerInfo, key)
    if row is not None and snapshot_at <= _aware_utc(row.snapshot_at):
        return False
    if row is None:
        row = HivePeerInfo(
            ruche_id=ruche_id,
            origin_instance_id=origin_instance_id,
            app_version=normalized["version"],
            hive_protocol=normalized["hive_protocol"],
            capabilities_json=normalized["capabilities"],
            snapshot_at=normalized["at"],
            received_at=moment,
        )
        db.add(row)
    else:
        row.app_version = normalized["version"]
        row.hive_protocol = normalized["hive_protocol"]
        row.capabilities_json = normalized["capabilities"]
        row.snapshot_at = normalized["at"]
        row.received_at = moment
    return True


@dataclass(frozen=True)
class PeerInfoView:
    ruche_id: bytes
    origin_instance_id: str
    app_version: str
    hive_protocol: int
    capabilities: list[str]
    snapshot_at: datetime
    received_at: datetime


def peer_info_row(db: Session, *, ruche_id: bytes, origin_instance_id: str) -> PeerInfoView | None:
    row = db.get(HivePeerInfo, (ruche_id, origin_instance_id))
    if row is None:
        return None
    caps = row.capabilities_json if isinstance(row.capabilities_json, list) else []
    clean_caps = [str(c) for c in caps if isinstance(c, str)]
    return PeerInfoView(
        ruche_id=ruche_id,
        origin_instance_id=origin_instance_id,
        app_version=str(row.app_version),
        hive_protocol=int(row.hive_protocol),
        capabilities=clean_caps,
        snapshot_at=row.snapshot_at,
        received_at=row.received_at,
    )


def peer_protocol(db: Session, *, ruche_id: bytes, origin_instance_id: str) -> int:
    if not is_active_member(db, ruche_id=ruche_id, instance_id=origin_instance_id):
        return 0
    view = peer_info_row(db, ruche_id=ruche_id, origin_instance_id=origin_instance_id)
    if view is None:
        return 0
    return int(view.hive_protocol)


def peer_supports(
    db: Session, *, ruche_id: bytes, origin_instance_id: str, capability: str
) -> bool:
    if capability not in KNOWN_HIVE_CAPABILITIES:
        return False
    if not is_active_member(db, ruche_id=ruche_id, instance_id=origin_instance_id):
        return False
    view = peer_info_row(db, ruche_id=ruche_id, origin_instance_id=origin_instance_id)
    if view is None:
        return False
    return capability in view.capabilities
