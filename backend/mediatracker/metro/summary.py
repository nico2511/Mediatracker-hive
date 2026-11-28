"""Agrégats lecture seule pour la carte Activité (fenêtre 24 h)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import TaskRun
from mediatracker.metro.collection import collection_status
from mediatracker.metro.detail import (
    build_edge_details,
    level1_interline_counts,
    rng5_exit_stats,
    station_outbound,
)
from mediatracker.metro.diagnostics import build_activity_diagnostics
from mediatracker.metro.graph import UNTRACEABLE_EDGES
from mediatracker.metro.ui_graph import VISIBLE_EDGE_IDS, VISIBLE_STATION_IDS

FROZEN_SECONDS = 120
SLOW_RATIO = 3.0
SLOW_MIN_MS = 30_000


@dataclass(slots=True)
class MetroThresholds:
    slow_ratio: float = SLOW_RATIO
    slow_min_ms: int = SLOW_MIN_MS
    frozen_seconds: int = FROZEN_SECONDS


def _median_ms(values: list[int]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _percentile_7d_median(db: Session, station: str, until: datetime) -> float | None:
    since = until - timedelta(days=7)
    rows = db.scalars(
        select(TaskRun.duration_ms).where(
            TaskRun.station == station,
            TaskRun.started_at >= since,
            TaskRun.started_at < until,
            TaskRun.duration_ms.is_not(None),
            TaskRun.duration_ms > 0,
        )
    ).all()
    durations = [int(r) for r in rows if r is not None]
    return _median_ms(durations)


def activity_summary(db: Session, *, hours: float = 24.0) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    collection = collection_status(db)
    rows = db.scalars(select(TaskRun).where(TaskRun.started_at >= since).order_by(TaskRun.started_at.desc())).all()

    def _aware(moment: datetime) -> datetime:
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment

    has_data = len(rows) > 0

    # KPI: rames en cours (séquences distinctes avec passage récent non terminé)
    trains_in_progress = len(
        {r.sequence_id for r in rows if r.sequence_id and r.finished_at is None and r.outcome != "blocked"}
    )

    # Blocages en cours
    blocked_open = [
        r
        for r in rows
        if r.outcome == "blocked"
        and _aware(r.heartbeat_at or r.started_at) >= now - timedelta(minutes=5)
    ]
    active_blocks = []
    seen_block_keys: set[tuple[str, str | None]] = set()

    for row in blocked_open:
        key = (row.station, row.blocked_by)
        if key in seen_block_keys:
            continue
        seen_block_keys.add(key)
        started = _aware(row.started_at)
        hb = _aware(row.heartbeat_at or row.started_at)
        minutes = int((now - started).total_seconds() // 60)
        active_blocks.append(
            {
                "station_id": row.station,
                "blocked_by": row.blocked_by,
                "edge_id": row.edge_id,
                "since": started.isoformat(),
                "minutes": minutes,
                "heartbeat_at": hb.isoformat() if hb else None,
            }
        )

    # Pass durations by station (24h)
    station_durations: dict[str, list[int]] = {}
    edge_hits: dict[str, int] = {}
    sequence_last_pass: dict[str, tuple[datetime, str | None, str | None]] = {}

    for row in rows:
        if row.duration_ms and row.duration_ms > 0:
            station_durations.setdefault(row.station, []).append(int(row.duration_ms))
        if row.edge_id and row.edge_id in VISIBLE_EDGE_IDS:
            edge_hits[row.edge_id] = edge_hits.get(row.edge_id, 0) + 1
        if row.sequence_id:
            prev = sequence_last_pass.get(row.sequence_id)
            started = _aware(row.started_at)
            if prev is None or started > prev[0]:
                sequence_last_pass[row.sequence_id] = (started, row.station, row.edge_id)

    pass_medians = [_median_ms(durs) for durs in station_durations.values() if durs]
    global_median_s = (statistics.median(pass_medians) / 1000.0) if pass_medians else None

    slow_stations: list[dict[str, Any]] = []
    station_stats: dict[str, dict[str, Any]] = {}
    for station_id in VISIBLE_STATION_IDS:
        durs = station_durations.get(station_id, [])
        median_24 = _median_ms(durs)
        ref_7d = _percentile_7d_median(db, station_id, now)
        count = len(durs)
        last_row = next((r for r in rows if r.station == station_id), None)
        frozen = False
        if last_row and last_row.outcome == "blocked":
            hb = _aware(last_row.heartbeat_at or last_row.started_at)
            frozen = (now - hb).total_seconds() > FROZEN_SECONDS
        slow = False
        if median_24 is not None and median_24 > SLOW_MIN_MS and ref_7d and median_24 >= SLOW_RATIO * ref_7d:
            slow = True
            slow_stations.append(
                {
                    "station_id": station_id,
                    "median_ms": int(median_24),
                    "baseline_ms": int(ref_7d),
                }
            )
        station_stats[station_id] = {
            "passes_24h": count,
            "median_ms": int(median_24) if median_24 is not None else None,
            "baseline_7d_ms": int(ref_7d) if ref_7d is not None else None,
            "slow": slow,
            "frozen": frozen,
            "last_at": last_row.started_at.isoformat() if last_row else None,
            "last_subject_ref": last_row.subject_ref if last_row else None,
        }

    # Correspondances empruntées
    edge_passes = sum(edge_hits.values())

    # Liens allumés: dernier passage (edge le plus récent avec edge_id) + blocage
    lit_edges: list[str] = []
    lit_pass: str | None = None
    for row in rows:
        if row.edge_id and row.edge_id in VISIBLE_EDGE_IDS and lit_pass is None:
            lit_pass = row.edge_id
            lit_edges.append(row.edge_id)
            break
    for block in active_blocks:
        eid = block.get("edge_id")
        if eid and eid not in lit_edges:
            lit_edges.append(eid)

    # Anomalies: diagnostic codes présents dans les causes (compte simple)
    anomaly_count = min(3, len([b for b in active_blocks]) + len(slow_stations))

    level1_edges = level1_interline_counts(rows, edge_hits)
    edge_details = build_edge_details(rows, edge_hits, since=since, until=now, lit_edges=lit_edges)
    station_details: dict[str, Any] = {}
    for station_id in ("RNG5", "ADB1", "DL1"):
        if station_id in VISIBLE_STATION_IDS:
            station_details[station_id] = {
                "outbound": station_outbound(rows, station_id, edge_hits, lit_edges=lit_edges),
            }

    diagnostics = build_activity_diagnostics(
        active_blocks=active_blocks,
        slow_stations=slow_stations,
        station_stats=station_stats,
    )
    exit_stats = rng5_exit_stats(rows, edge_hits)

    return {
        "hours": hours,
        "since": since.isoformat(),
        "until": now.isoformat(),
        "collection": collection,
        "has_data": has_data,
        "kpis": {
            "trains_in_progress": trains_in_progress,
            "active_blocks": len(active_blocks),
            "slow_stations": len(slow_stations),
            "network_anomalies": anomaly_count,
            "edge_passes_24h": edge_passes,
            "median_pass_s": round(global_median_s, 1) if global_median_s is not None else None,
            "untraceable_links": len(UNTRACEABLE_EDGES),
        },
        "active_blocks": active_blocks,
        "slow_stations": slow_stations,
        "station_stats": station_stats,
        "edge_hits": edge_hits,
        "lit_edges": lit_edges,
        "lit_pass_edge": lit_pass,
        "lit_block_edges": [b["edge_id"] for b in active_blocks if b.get("edge_id")],
        "level1_edges": level1_edges,
        "edge_details": edge_details,
        "station_details": station_details,
        "diagnostics": diagnostics,
        "rng5_exit_stats": exit_stats,
        "thresholds": {
            "slow_ratio": SLOW_RATIO,
            "slow_min_ms": SLOW_MIN_MS,
            "frozen_seconds": FROZEN_SECONDS,
        },
    }
