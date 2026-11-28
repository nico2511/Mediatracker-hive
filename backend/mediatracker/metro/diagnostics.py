"""Constats carte Activité : données collectées + notices structurelles (§6)."""

from __future__ import annotations

from typing import Any

from mediatracker.metro.graph import STATIONS

# Points connus du code — sans durée ni effectif inventé (plan §6).
CODEBASE_NOTICES: tuple[dict[str, Any], ...] = (
    {
        "id": "R1",
        "tag": "R1",
        "tag_class": "warn",
        "title": "Film obtenu en repli : sort du suivi",
        "detail": "La version principale n'est plus proposée.",
        "code": "core/pipeline/queue.py:568",
        "station_id": "RNG5",
    },
    {
        "id": "G1",
        "tag": "G1",
        "tag_class": "warn",
        "title": "Catalogue Ruche resynchronisé à chaque passe",
        "detail": "Coût qui grandit avec la bibliothèque.",
        "code": "worker.py:112 · hive/catalog.py:750-816",
        "station_id": "RUC2",
    },
    {
        "id": "G2",
        "tag": "G2",
        "tag_class": "warn",
        "title": "Base occupée",
        "detail": "SQLite verrouillé — étape reportée.",
        "code": "worker.py:55-58, 226-228",
        "station_id": "SOC7",
    },
    {
        "id": "A2",
        "tag": "A2",
        "tag_class": "warn",
        "title": "Une seule débride à la fois",
        "detail": "La file attend AllDebrid séquentiellement.",
        "code": "core/pipeline/queue.py:871-889",
        "station_id": "DL1",
    },
)

LINE_FOCUS_STATION: dict[str, str] = {
    "Sources": "SRC1",
    "Suivi": "SUI3",
    "AllDebrid": "ADB1",
    "Telechargements": "DL1",
    "Rangement": "RNG5",
    "Bibliotheque": "BIB2",
    "Jellyfin": "JF2",
    "TMDB": "TMDB1",
    "Ruche": "RUC2",
    "Corbeille": "CRB2",
    "Notifications": "NOT1",
    "Socle": "SOC1",
}


def _station_label(station_id: str) -> str:
    meta = STATIONS.get(station_id)
    return meta[1] if meta else station_id


def build_activity_diagnostics(
    *,
    active_blocks: list[dict[str, Any]],
    slow_stations: list[dict[str, Any]],
    station_stats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Constats issus de task_run + notices code statiques (sans scénario fictif)."""
    dynamic: list[dict[str, Any]] = []
    seen_block: set[str] = set()
    for block in active_blocks:
        station_id = block.get("station_id") or ""
        key = f"{station_id}:{block.get('blocked_by')}"
        if key in seen_block:
            continue
        seen_block.add(key)
        label = _station_label(station_id)
        minutes = block.get("minutes")
        duration = f"depuis {minutes} min" if minutes is not None else "en cours"
        dynamic.append(
            {
                "id": f"block-{key}",
                "tag": "Blocage",
                "tag_class": "danger",
                "title": f"{label} — blocage {duration}",
                "detail": block.get("blocked_by") or "Passage bloqué",
                "code": block.get("edge_id") or "",
                "station_id": station_id,
                "edge_id": block.get("edge_id"),
            }
        )
    for slow in slow_stations:
        station_id = slow.get("station_id") or ""
        label = _station_label(station_id)
        median_ms = slow.get("median_ms")
        baseline_ms = slow.get("baseline_ms")
        detail_parts = []
        if median_ms is not None:
            detail_parts.append(f"médiane 24 h : {median_ms / 1000:.1f} s")
        if baseline_ms is not None:
            detail_parts.append(f"réf. 7 j : {baseline_ms / 1000:.1f} s")
        dynamic.append(
            {
                "id": f"slow-{station_id}",
                "tag": "Lenteur",
                "tag_class": "warn",
                "title": f"{label} — station lente",
                "detail": " · ".join(detail_parts) if detail_parts else "Dépasse le seuil configuré.",
                "code": "",
                "station_id": station_id,
            }
        )
    for station_id, stats in station_stats.items():
        if not stats.get("frozen"):
            continue
        label = _station_label(station_id)
        dynamic.append(
            {
                "id": f"frozen-{station_id}",
                "tag": "Figée",
                "tag_class": "warn",
                "title": f"{label} — station figée",
                "detail": "Aucun heartbeat récent sur un blocage ouvert.",
                "code": "",
                "station_id": station_id,
            }
        )
    return dynamic + list(CODEBASE_NOTICES)