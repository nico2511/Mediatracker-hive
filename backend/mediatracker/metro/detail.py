"""Détails panneaux : frises, passages, cascade (lecture seule)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from mediatracker.db.models import TaskRun
from mediatracker.metro.graph import EDGE_BY_ID, STATIONS
from mediatracker.metro.ui_graph import CORRESPONDENCE_CASCADE, RNG5_EXITS, VISIBLE_EDGE_IDS


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def level1_interline_counts(
    rows: list[TaskRun],
    edge_hits: dict[str, int],
) -> list[dict[str, Any]]:
    """Agrège les passages edge_id sur 24 h en liens entre lignes (noms internes)."""
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for edge_id, count in edge_hits.items():
        edge = EDGE_BY_ID.get(edge_id)
        if edge is None:
            continue
        line_from = STATIONS.get(edge.origin, ("Socle", ""))[0]
        line_to = STATIONS.get(edge.dest, ("Socle", ""))[0]
        if line_from == line_to:
            continue
        totals[(line_from, line_to)] += int(count)
    return [
        {"from": pair[0], "to": pair[1], "count": count, "count_label": str(count)}
        for pair, count in sorted(totals.items(), key=lambda item: -item[1])
    ]


def _bucket_counts(
    rows: list[TaskRun],
    *,
    since: datetime,
    until: datetime,
    bucket_minutes: int,
    edge_id: str | None = None,
    blocked_only: bool = False,
) -> list[dict[str, Any]]:
    span = max(bucket_minutes * 60, 1)
    start = _aware(since)
    end = _aware(until)
    buckets: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"passes": 0, "blocked": 0, "durations_ms": []}
    )
    for row in rows:
        if edge_id and row.edge_id != edge_id:
            continue
        t = _aware(row.started_at)
        if t < start or t > end:
            continue
        idx = int((t - start).total_seconds() // span)
        if blocked_only or row.outcome == "blocked":
            buckets[idx]["blocked"] += 1
        else:
            buckets[idx]["passes"] += 1
        if row.duration_ms and row.duration_ms > 0:
            buckets[idx]["durations_ms"].append(int(row.duration_ms))
    total_slots = int((end - start).total_seconds() // span) + 1
    out: list[dict[str, Any]] = []
    for idx in range(total_slots):
        slot_start = start + timedelta(seconds=idx * span)
        stats = buckets.get(idx, {"passes": 0, "blocked": 0, "durations_ms": []})
        durations = stats.get("durations_ms") or []
        median_ms = int(statistics.median(durations)) if durations else None
        out.append(
            {
                "at": slot_start.isoformat(),
                "passes": stats["passes"],
                "blocked": stats["blocked"],
                "median_ms": median_ms,
            }
        )
    return out


def rng5_exit_stats(rows: list[TaskRun], edge_hits: dict[str, int]) -> dict[str, dict[str, Any]]:
    """Passages / 24 h et médiane par arête de sortie Finalisation (RNG5)."""
    out: dict[str, dict[str, Any]] = {}
    for exit_row in RNG5_EXITS:
        edge_id = exit_row["edge_id"]
        durations = [
            int(r.duration_ms)
            for r in rows
            if r.edge_id == edge_id and r.duration_ms and r.duration_ms > 0
        ]
        median_ms = int(statistics.median(durations)) if durations else None
        out[edge_id] = {
            "passes_24h": edge_hits.get(edge_id, 0),
            "median_ms": median_ms,
        }
    return out


def edge_detail(
    rows: list[TaskRun],
    edge_id: str,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    edge = EDGE_BY_ID.get(edge_id)
    if edge is None:
        return {}
    recent = []
    for row in rows:
        if row.edge_id != edge_id and not (
            edge_id == "C06" and row.station == "DL1" and row.outcome == "blocked"
        ):
            continue
        recent.append(row)
        if len(recent) >= 8:
            break
    passages = []
    for row in recent[:5]:
        started = _aware(row.started_at)
        duration_min = None
        if row.finished_at:
            duration_min = int((_aware(row.finished_at) - started).total_seconds() // 60)
        elif row.heartbeat_at:
            duration_min = int((_aware(row.heartbeat_at) - started).total_seconds() // 60)
        passages.append(
            {
                "time": started.strftime("%H:%M"),
                "subject_ref": row.subject_ref or "—",
                "effect": row.outcome,
                "duration_min": duration_min,
            }
        )
    timeline_24h = _bucket_counts(rows, since=since, until=until, bucket_minutes=60, edge_id=edge_id)
    zoom_start = until - timedelta(hours=3)
    timeline_3h = _bucket_counts(
        rows,
        since=zoom_start,
        until=until,
        bucket_minutes=15,
        edge_id=edge_id,
    )
    blocked_24h = _bucket_counts(
        rows,
        since=since,
        until=until,
        bucket_minutes=60,
        edge_id=edge_id,
        blocked_only=True,
    )
    cascade_ids = CORRESPONDENCE_CASCADE.get(edge_id, ())
    cascade = []
    for station_id in cascade_ids:
        meta = STATIONS.get(station_id)
        if not meta:
            continue
        line_name, label = meta
        cascade.append({"station_id": station_id, "line": line_name, "label": label})
    block_minutes = 0
    for row in rows:
        if row.edge_id == edge_id and row.outcome == "blocked":
            block_minutes = int((_aware(until) - _aware(row.started_at)).total_seconds() // 60)
            break
    return {
        "edge_id": edge_id,
        "timeline_24h": timeline_24h,
        "timeline_3h": timeline_3h,
        "timeline_blocked_24h": blocked_24h,
        "passages_recent": passages,
        "cascade": cascade,
        "block_minutes": block_minutes,
    }


def station_outbound(
    rows: list[TaskRun],
    station_id: str,
    edge_hits: dict[str, int],
    *,
    lit_edges: list[str],
) -> list[dict[str, Any]]:
    outbound: list[dict[str, Any]] = []
    for edge_id in VISIBLE_EDGE_IDS:
        edge = EDGE_BY_ID.get(edge_id)
        if edge is None or edge.origin != station_id:
            continue
        dest_meta = STATIONS.get(edge.dest)
        dest_label = dest_meta[1] if dest_meta else edge.dest
        line = dest_meta[0] if dest_meta else ""
        outbound.append(
            {
                "edge_id": edge_id,
                "kind": edge.kind,
                "dest_id": edge.dest,
                "dest_label": dest_label,
                "dest_line": line,
                "passes_24h": edge_hits.get(edge_id, 0),
                "lit": edge_id in lit_edges,
                "untraceable": edge_id in {"C20", "C49", "C56", "C02"},
            }
        )
    outbound.sort(key=lambda row: -row["passes_24h"])
    return outbound


def build_edge_details(
    rows: list[TaskRun],
    edge_hits: dict[str, int],
    *,
    since: datetime,
    until: datetime,
    lit_edges: list[str],
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    targets = set(edge_hits) | set(lit_edges) | {"C06", "C16"}
    for edge_id in targets:
        if edge_id not in EDGE_BY_ID:
            continue
        details[edge_id] = edge_detail(rows, edge_id, since=since, until=until)
    return details