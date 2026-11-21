"""État Hive pour l'interface fiche (partage, transferts, provenance fichier)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.file_status import FileStatusIndex
from mediatracker.core.pipeline.download_guard import DOWNLOAD_TERMINAL_STATES
from mediatracker.db.models import Download, DownloadHiveFetch, HiveFetchGrant, MediaItem
from mediatracker.hive.download_aria2 import hive_download_active_for_item
from mediatracker.hive.signal import item_file_present
from mediatracker.hive.transfer_parallel import TransferParallelGate


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def item_received_via_hive(db: Session, item: MediaItem) -> bool:
    if item.id is None or not item_file_present(db, item):
        return False
    row = db.scalar(
        select(Download.id)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(Download.media_item_id == item.id, Download.state == "done")
        .order_by(Download.finished_at.desc())
        .limit(1)
    )
    return row is not None


def inbound_transfer_progress(db: Session, *, sha: str, item: MediaItem) -> tuple[bool, float | None]:
    if not sha or item.id is None:
        return False, None
    if not hive_download_active_for_item(db, item, sha):
        return False, None
    row = db.scalar(
        select(Download)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(
            Download.media_item_id == item.id,
            DownloadHiveFetch.sha256_hex == str(sha).lower(),
            Download.state.in_(("pending", "resolving", "queued", "downloading", "post_processing")),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )
    if row is None:
        return True, None
    expected = max(int(row.size or 0), 1)
    done = max(0, int(row.progress or 0))
    return True, min(100.0, round(100.0 * done / expected, 1))


def _active_grants_for_item(db: Session, item_id: int, *, now: datetime) -> list[HiveFetchGrant]:
    moment = _aware_utc(now)
    rows = list(
        db.scalars(
            select(HiveFetchGrant).where(
                HiveFetchGrant.media_item_id == item_id,
                HiveFetchGrant.revoked_at.is_(None),
            )
        ).all()
    )
    return [row for row in rows if _aware_utc(row.expires_at) > moment]


def outbound_transfer_progress(
    db: Session,
    item: MediaItem,
    *,
    now: datetime,
    parallel_gate: TransferParallelGate | None,
) -> tuple[bool, float | None]:
    if item.id is None:
        return False, None
    grants = _active_grants_for_item(db, item.id, now=now)
    if not grants:
        return False, None
    active = False
    best_pct: float | None = None
    for grant in grants:
        grant_id = int(grant.id)
        if parallel_gate is not None and parallel_gate.is_grant_active(grant_id):
            active = True
            pct = parallel_gate.transfer_pct_for_grant(grant_id, int(grant.content_size or 0))
            if pct is not None and (best_pct is None or pct > best_pct):
                best_pct = pct
    return active, best_pct


def batch_inbound_transfer_ids(
    db: Session, item_ids: list[int], sha_by_item: dict[int, str]
) -> dict[int, tuple[bool, float | None]]:
    if not item_ids or not sha_by_item:
        return {}
    rows = list(
        db.execute(
            select(Download.media_item_id, Download.progress, Download.size, DownloadHiveFetch.sha256_hex)
            .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
            .where(
                Download.media_item_id.in_(item_ids),
                Download.state.not_in(tuple(DOWNLOAD_TERMINAL_STATES)),
            )
        ).all()
    )
    out: dict[int, tuple[bool, float | None]] = {}
    for media_item_id, progress, size, fetch_sha in rows:
        expected_sha = sha_by_item.get(media_item_id)
        if not expected_sha or (fetch_sha or "").lower() != expected_sha:
            continue
        expected = max(int(size or 0), 1)
        done = max(0, int(progress or 0))
        pct = min(100.0, round(100.0 * done / expected, 1))
        out[media_item_id] = (True, pct)
    return out


def item_file_present_from_index(idx: FileStatusIndex, item: MediaItem) -> bool:
    """Même critère que item_file_present, sans recharger l’index."""
    if item.id is None:
        return False
    if idx.for_item(item) == "present":
        return True
    if item.downloaded and item.file_path:
        return Path(item.file_path).is_file()
    return False


def batch_item_file_present(db: Session, items: list[MediaItem]) -> set[int]:
    if not items:
        return set()
    idx = FileStatusIndex.load(db, items)
    return {
        item.id
        for item in items
        if item.id is not None and item_file_present_from_index(idx, item)
    }


def batch_received_via_hive(
    db: Session, item_ids: list[int], *, present_ids: set[int] | None = None
) -> set[int]:
    if not item_ids:
        return set()
    rows = db.scalars(
        select(Download.media_item_id)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(Download.media_item_id.in_(item_ids), Download.state == "done")
        .distinct()
    ).all()
    if present_ids is None:
        items = list(db.scalars(select(MediaItem).where(MediaItem.id.in_(item_ids))).all())
        present_ids = batch_item_file_present(db, items)
    return {int(row) for row in rows if row is not None and int(row) in present_ids}


def batch_outbound_transfer_progress(
    db: Session,
    item_ids: list[int],
    *,
    now: datetime,
    parallel_gate: TransferParallelGate | None,
) -> dict[int, tuple[bool, float | None]]:
    if not item_ids:
        return {}
    moment = _aware_utc(now)
    rows = list(
        db.scalars(
            select(HiveFetchGrant).where(
                HiveFetchGrant.media_item_id.in_(item_ids),
                HiveFetchGrant.revoked_at.is_(None),
            )
        ).all()
    )
    out: dict[int, tuple[bool, float | None]] = {}
    for row in rows:
        if row.media_item_id is None or _aware_utc(row.expires_at) <= moment:
            continue
        item_id = int(row.media_item_id)
        active, best_pct = out.get(item_id, (False, None))
        if parallel_gate is not None and parallel_gate.is_grant_active(int(row.id)):
            active = True
            pct = parallel_gate.transfer_pct_for_grant(int(row.id), int(row.content_size or 0))
            if pct is not None and (best_pct is None or pct > best_pct):
                best_pct = pct
        out[item_id] = (active, best_pct)
    return out


@dataclass(frozen=True)
class ShareUiBatchFields:
    has_file: bool
    received_via_hive: bool
    inbound_active: bool
    inbound_pct: float | None
    outbound_active: bool
    outbound_pct: float | None


def batch_share_ui_fields(
    db: Session,
    items: list[MediaItem],
    *,
    sha_by_item: dict[int, str],
    now: datetime,
    parallel_gate: TransferParallelGate | None,
) -> dict[int, ShareUiBatchFields]:
    """Champs fiche Hive calculés en requêtes groupées (pas de N+1 par épisode)."""
    item_ids = [item.id for item in items if item.id is not None]
    present_ids = batch_item_file_present(db, items)
    received_ids = batch_received_via_hive(db, item_ids, present_ids=present_ids)
    inbound = batch_inbound_transfer_ids(db, item_ids, sha_by_item)
    outbound = batch_outbound_transfer_progress(
        db, item_ids, now=now, parallel_gate=parallel_gate
    )
    out: dict[int, ShareUiBatchFields] = {}
    for item_id in item_ids:
        in_active, in_pct = inbound.get(item_id, (False, None))
        out_active, out_pct = outbound.get(item_id, (False, None))
        out[item_id] = ShareUiBatchFields(
            has_file=item_id in present_ids,
            received_via_hive=item_id in received_ids,
            inbound_active=in_active,
            inbound_pct=in_pct,
            outbound_active=out_active,
            outbound_pct=out_pct,
        )
    return out
