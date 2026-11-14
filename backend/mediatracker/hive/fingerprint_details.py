"""États empreinte fichier / morceaux pour le volet UI (lecture cohérence, pas certification disque)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveFileFingerprint, HiveFingerprintQueue, MediaItem
from mediatracker.hive.chunk_digest_store import (
    HIVE_CHUNK_DIGESTS_REQUIRED,
    chunk_digests_row_valid,
    expected_chunk_count,
    fingerprint_needs_chunk_backfill,
)
from mediatracker.hive.share_scope import item_has_local_file, scope_media_items


@dataclass(frozen=True)
class RecalculateScopeResult:
    scheduled_chunks: int
    in_progress_chunks: int
    file_sha_queued: int
    skipped_no_intent: int
    skipped_no_fingerprint: int
    claims_recovered: int
    wake_sent: bool


@dataclass(frozen=True)
class FingerprintRowOut:
    media_item_id: int
    season: int | None
    episode: int | None
    slot_label: str
    file_state: str
    file_detail: str
    chunk_state: str
    chunk_detail: str
    phase: str
    chunk_progress_pct: float | None
    metadata_inconsistent: bool


def _slot_label(item: MediaItem) -> str:
    if item.season is not None and item.episode is not None:
        return f"S{item.season:02d}E{item.episode:02d}"
    return "Fichier"


def _file_states(fp: HiveFileFingerprint | None, item: MediaItem) -> tuple[str, str]:
    if not item_has_local_file(item):
        return "absent", "Aucun fichier local"
    if fp is None:
        return "present", "Fichier présent · empreinte non démarrée"
    err = (fp.hash_error or "").strip()
    if err:
        return "read_error", "Fichier · erreur lecture"
    sha = (fp.sha256_hex or "").strip()
    if not sha:
        partial = int(fp.partial_size or 0)
        total = int(fp.size or 0)
        if total > 0 and partial > 0:
            pct = min(99, max(0, round(100.0 * partial / total)))
            return "calculating", f"Empreinte fichier · calcul {pct} %"
        return "pending", "Fichier présent · empreinte en attente"
    return "ready", "Empreinte fichier · enregistrée"


def _chunk_states(
    db: Session, fp: HiveFileFingerprint | None, *, file_ready: bool
) -> tuple[str, str, float | None, bool]:
    if not HIVE_CHUNK_DIGESTS_REQUIRED:
        if not file_ready or fp is None:
            return "absent", "Morceaux · empreinte fichier requise", None, False
        digests = chunk_digests_row_valid(fp)
        if digests is not None:
            return "valid", "Morceaux · présents (legacy)", None, False
        return "optional", "Morceaux · non requis pour les transferts", None, False
    if not file_ready or fp is None:
        return "absent", "Morceaux · empreinte fichier requise", None, False
    bound = (fp.chunk_digests_sha256_hex or "").strip().lower()
    sha = (fp.sha256_hex or "").strip().lower()
    if bound and sha and bound != sha:
        return "mismatch", "Morceaux · liaison incohérente", None, True
    digests = chunk_digests_row_valid(fp)
    if digests is not None:
        return "valid", "Morceaux · cohérents en base", None, False
    size = int(fp.size or 0)
    expected = expected_chunk_count(size)
    offset = int(fp.chunk_digest_offset or 0)
    inconsistent = False
    raw = fp.chunk_digests_json
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                if len(parsed) != expected:
                    inconsistent = True
        except json.JSONDecodeError:
            inconsistent = True
    if fp.chunk_digest_claimed_at is not None:
        if offset > 0 and size > 0 and offset < size:
            pct = round(100.0 * offset / size, 1)
            return "calculating", f"Morceaux · calcul {pct} %", pct, inconsistent
        return "queued", "Morceaux · calcul en cours", None, inconsistent
    queue = db.scalar(
        select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == fp.media_item_id)
    )
    if queue is not None and queue.status in ("pending", "running"):
        if offset > 0 and size > 0:
            pct = round(100.0 * min(offset, size) / size, 1)
            if offset < size:
                return "calculating", f"Morceaux · calcul {pct} %", pct, inconsistent
        return "queued", "Morceaux · en file", None, inconsistent
    if offset > 0 and size > 0 and offset < size:
        pct = round(100.0 * offset / size, 1)
        return "incomplete", f"Morceaux · incomplets {pct} %", pct, inconsistent
    if inconsistent:
        return "unverified", "Morceaux · à contrôler", None, True
    if fingerprint_needs_chunk_backfill(fp):
        return "absent", "Morceaux · absents", None, False
    return "unverified", "Morceaux · non vérifiés", None, inconsistent


def _rollup_phase(
    file_state: str,
    chunk_state: str,
    *,
    metadata_inconsistent: bool,
) -> str:
    if file_state == "read_error":
        return "erreur"
    if metadata_inconsistent or chunk_state == "mismatch":
        return "ecart"
    if file_state == "ready" and chunk_state in ("valid", "optional"):
        return "pret"
    if chunk_state in ("queued",) or file_state == "pending":
        return "en_file"
    if chunk_state == "calculating" or file_state == "calculating":
        return "calcul"
    if chunk_state in ("absent", "incomplete") or file_state == "pending":
        return "en_attente"
    if chunk_state == "unverified":
        return "a_controler"
    if file_state == "ready" and chunk_state != "valid":
        return "en_attente"
    return "en_attente"


def fingerprint_rows_for_scope(
    db: Session, media_id: int, *, season: int | None = None
) -> list[FingerprintRowOut]:
    rows: list[FingerprintRowOut] = []
    for item in scope_media_items(db, media_id, season=season):
        assert item.id is not None
        fp = db.get(HiveFileFingerprint, item.id)
        file_state, file_detail = _file_states(fp, item)
        file_ready = file_state == "ready"
        chunk_state, chunk_detail, chunk_pct, meta_bad = _chunk_states(db, fp, file_ready=file_ready)
        phase = _rollup_phase(file_state, chunk_state, metadata_inconsistent=meta_bad)
        rows.append(
            FingerprintRowOut(
                media_item_id=int(item.id),
                season=item.season,
                episode=item.episode,
                slot_label=_slot_label(item),
                file_state=file_state,
                file_detail=file_detail,
                chunk_state=chunk_state,
                chunk_detail=chunk_detail,
                phase=phase,
                chunk_progress_pct=chunk_pct,
                metadata_inconsistent=meta_bad,
            )
        )
    return rows


def empreintes_summary(rows: list[FingerprintRowOut]) -> dict[str, int]:
    ready = sum(1 for r in rows if r.phase == "pret")
    pending = len(rows) - ready
    return {"ready_count": ready, "pending_count": pending, "total": len(rows)}


def verify_fingerprint_metadata(db: Session, media_id: int, *, season: int | None = None) -> list[FingerprintRowOut]:
    """Relit l'état en base sans écriture (contrôle cohérence métadonnées)."""
    return fingerprint_rows_for_scope(db, media_id, season=season)


def request_chunk_recalculate(
    db: Session,
    settings,
    media_id: int,
    *,
    season: int | None,
    moment: datetime | None = None,
) -> RecalculateScopeResult:
    """Programme le travail éligible dans le périmètre ; ne casse pas un claim actif récent."""
    from mediatracker.hive.chunk_digest_store import (
        HIVE_CHUNK_DIGESTS_REQUIRED,
        chunk_digest_claim_is_stale,
        list_backfill_candidate_ids_for_items,
        recover_stale_chunk_claim_for_item,
    )
    from mediatracker.hive.fingerprint_queue import enqueue_fingerprint_work
    from mediatracker.hive.fingerprint_wake import wake_fingerprint_daemon
    from mediatracker.hive.share_policy import item_wants_hive_share

    now = moment or datetime.now(timezone.utc)
    scope_ids: list[int] = []
    skipped_no_intent = 0
    skipped_no_fingerprint = 0
    in_progress = 0
    file_sha_queued = 0
    claims_recovered = 0

    for item in scope_media_items(db, media_id, season=season):
        assert item.id is not None
        item_id = int(item.id)
        scope_ids.append(item_id)
        if not item_wants_hive_share(db, item_id):
            skipped_no_intent += 1
            continue
        fp = db.get(HiveFileFingerprint, item_id)
        if fp is None:
            skipped_no_fingerprint += 1
            continue
        sha = (fp.sha256_hex or "").strip()
        if not sha:
            enqueue_fingerprint_work(db, item_id, now=now)
            file_sha_queued += 1
            continue
        if not HIVE_CHUNK_DIGESTS_REQUIRED:
            continue
        if not fingerprint_needs_chunk_backfill(fp):
            continue
        if fp.chunk_digest_claimed_at is not None:
            if chunk_digest_claim_is_stale(fp.chunk_digest_claimed_at, now=now):
                if recover_stale_chunk_claim_for_item(db, item_id, now=now):
                    claims_recovered += 1
            else:
                in_progress += 1

    scheduled = (
        len(list_backfill_candidate_ids_for_items(db, scope_ids, now=now))
        if HIVE_CHUNK_DIGESTS_REQUIRED
        else 0
    )

    wake_sent = False
    if scheduled > 0 or file_sha_queued > 0:
        wake_fingerprint_daemon(db, settings, commit=False)
        wake_sent = True

    return RecalculateScopeResult(
        scheduled_chunks=scheduled,
        in_progress_chunks=in_progress,
        file_sha_queued=file_sha_queued,
        skipped_no_intent=skipped_no_intent,
        skipped_no_fingerprint=skipped_no_fingerprint,
        claims_recovered=claims_recovered,
        wake_sent=wake_sent,
    )
