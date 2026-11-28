"""Graphe UI : stations visibles, disposition et mécanismes (aligné maquettes 1440/390)."""

from __future__ import annotations

from typing import Any

from mediatracker.metro.diagnostics import LINE_FOCUS_STATION
from mediatracker.metro.graph import (
    EDGE_BY_ID,
    LINE_COLORS,
    STATIONS,
    UNTRACEABLE_EDGES,
    MetroEdge,
)

LineNumber = int

LINE_ORDER: tuple[tuple[LineNumber, str], ...] = (
    (1, "Sources"),
    (2, "Suivi"),
    (3, "AllDebrid"),
    (4, "Telechargements"),
    (5, "Rangement"),
    (6, "Bibliotheque"),
    (7, "Jellyfin"),
    (8, "TMDB"),
    (9, "Ruche"),
    (10, "Corbeille"),
    (11, "Notifications"),
    (12, "Socle"),
)

LINE_NUMBERS: dict[str, LineNumber] = {name: num for num, name in LINE_ORDER}

VISIBLE_STATION_IDS: frozenset[str] = frozenset(
    {
        "SOC1",
        "SOC7",
        "TMDB1",
        "TMDB3",
        "SUI1",
        "SUI5",
        "SUI3",
        "SUI6",
        "SUI7",
        "SRC1",
        "SRC3",
        "SRC4",
        "ADB2",
        "ADB1",
        "ADB3",
        "DL1",
        "DL2",
        "RNG1",
        "RNG5",
        "RUC2",
        "RUC1",
        "RUC5",
        "RUC9",
        "RUC3",
        "CRB2",
        "CRB3",
        "BIB3",
        "BIB2",
        "JF2",
        "JF1",
        "JF4",
        "NOT1",
        "NOT2",
    }
)

EDGE_STATION_ALIAS: dict[str, str] = {
    "CRB1": "CRB2",
    "SRC8": "SRC1",
    "SRC5": "ADB2",
    "SRC6": "SRC4",
    "RUC8": "RUC3",
    "RUC6": "SUI1",
    "RUC7": "RUC2",
    "RUC12": "RUC9",
    "RUC11": "RUC9",
    "RUC10": "RUC9",
    "CRB6": "CRB3",
    "BIB1": "BIB2",
    "BIB4": "BIB2",
    "JF3": "JF2",
    "JF5": "JF4",
    "TMDB2": "TMDB1",
    "NOT3": "NOT2",
    "NOT5": "NOT4",
    "SOC2": "SOC1",
    "SOC3": "SOC1",
    "DL3": "DL2",
    "DL4": "DL2",
    "DL5": "DL1",
    "DL7": "DL1",
    "DL8": "DL1",
    "DL9": "DL1",
    "RNG4": "RNG1",
    "RNG6": "RNG5",
    "RNG2": "RNG1",
    "RNG3": "RNG5",
    "SUI4": "SUI3",
    "SUI2": "SUI1",
    "ADB4": "ADB3",
    "SRC10": "SRC1",
    "SRC9": "SRC3",
    "SRC7": "SRC4",
    "SRC2": "SRC1",
    "RUC4": "RUC3",
    "RUC13": "RUC3",
}


def _map_station(station_id: str) -> str | None:
    if station_id in VISIBLE_STATION_IDS:
        return station_id
    alias = EDGE_STATION_ALIAS.get(station_id)
    if alias and alias in VISIBLE_STATION_IDS:
        return alias
    return None


VISIBLE_EDGE_IDS: tuple[str, ...] = (
    "C01",
    "C02",
    "C03",
    "C04",
    "C05",
    "C06",
    "C07",
    "C08",
    "C10",
    "C13",
    "C14",
    "C15",
    "C16",
    "C17",
    "C18",
    "C19",
    "C23",
    "C25",
    "C26",
    "C27",
    "C33",
    "C36",
    "C40",
    "C41",
    "C45",
    "C49",
    "C50",
    "C52",
    "C54",
    "C55",
    "C57",
    "C61",
    "C62",
    "C63",
    "C66",
    "C67",
)

STATION_POSITIONS: dict[str, tuple[float, float]] = {
    "SOC1": (95, 52),
    "SOC7": (265, 52),
    "TMDB1": (95, 140),
    "TMDB3": (265, 140),
    "SUI1": (358, 218),
    "SUI5": (462, 226),
    "SUI3": (578, 218),
    "SUI6": (712, 226),
    "SUI7": (872, 218),
    "SRC1": (44, 348),
    "SRC3": (182, 370),
    "SRC4": (318, 348),
    "ADB2": (358, 360),
    "ADB1": (358, 288),
    "ADB3": (358, 360),
    "DL1": (438, 360),
    "DL2": (518, 360),
    "RNG1": (598, 360),
    "RNG5": (692, 360),
    "RUC2": (800, 390),
    "RUC1": (875, 465),
    "RUC5": (725, 465),
    "RUC9": (747, 518),
    "RUC3": (800, 540),
    "CRB2": (480, 480),
    "CRB3": (610, 480),
    "BIB3": (185, 610),
    "BIB2": (345, 610),
    "JF2": (470, 690),
    "JF1": (575, 690),
    "JF4": (690, 690),
    "NOT1": (120, 760),
    "NOT2": (320, 760),
}

LEVEL1_POSITIONS: dict[str, tuple[float, float]] = {
    "Sources": (130, 340),
    "Suivi": (620, 150),
    "AllDebrid": (330, 235),
    "Telechargements": (420, 390),
    "Rangement": (640, 390),
    "Bibliotheque": (170, 580),
    "Jellyfin": (400, 700),
    "TMDB": (330, 80),
    "Ruche": (835, 455),
    "Corbeille": (600, 580),
    "Notifications": (760, 710),
    "Socle": (120, 80),
}

STATION_LABEL_LAYOUT: dict[str, dict[str, float | str]] = {
    "SRC1": {"label_dy": -28, "label_size": 11.5, "label_anchor": "start", "label_dx": -4},
    "SRC3": {"label_dy": 34, "label_size": 11.5, "label_anchor": "middle", "label_dx": 0},
    "SRC4": {"label_dy": -28, "label_size": 11.5, "label_anchor": "end", "label_dx": 4},
    "SUI1": {"label_dy": -22, "label_size": 11},
    "SUI5": {"label_dy": 26, "label_size": 10},
    "SUI3": {"label_dy": -22, "label_size": 10},
    "SUI6": {"label_dy": 26, "label_size": 10},
    "SUI7": {"label_dy": -22, "label_size": 10},
    "ADB1": {"label_dy": -22, "label_size": 11},
    "ADB2": {"label_dy": 24, "label_size": 10},
    "ADB3": {"label_dy": -22, "label_size": 10},
}

RNG5_MECHANISMS: tuple[dict[str, Any], ...] = (
    {
        "id": "RNG1",
        "label": "Import prêt",
        "code": "queue.py:726",
        "refs": "core/pipeline/queue.py:726, 741",
    },
    {
        "id": "RNG2",
        "label": "Vérif. fichier reçu de la ruche",
        "code": "download_pipeline.py:444",
        "refs": "hive/download_pipeline.py:444",
    },
    {
        "id": "RNG3",
        "label": "Contrôle d'intégrité",
        "code": "filing_integrity.py:15",
        "refs": "core/filing_integrity.py:15",
    },
    {
        "id": "RNG4",
        "label": "Garder le meilleur",
        "code": "queue.py:403",
        "refs": "core/pipeline/queue.py:403, 670",
    },
    {
        "id": "RNG5",
        "label": "Finalisation",
        "code": "queue.py:528",
        "refs": "core/pipeline/queue.py:528",
        "pole": True,
    },
)

RNG5_EXITS: tuple[dict[str, Any], ...] = (
    {"order": 1, "edge_id": "C16", "dest": "RUC2", "line_color": "#C9910D"},
    {"order": 2, "edge_id": "C13", "dest": "SUI3", "line_color": "#007852", "anomaly": "R1"},
    {"order": 3, "edge_id": "C15", "dest": "CRB1", "line_color": "#704B1C", "display_dest": "Classement"},
    {"order": 4, "edge_id": "C18", "dest": "SUI6", "line_color": "#007852", "anomaly": "R1"},
    {"order": 5, "edge_id": "C17", "dest": "NOT1", "line_color": "#FA9ABA"},
    {"order": 6, "edge_id": "C14", "dest": "JF1", "line_color": "#62259D"},
    {"order": 7, "edge_id": "C19", "dest": "BIB2", "line_color": "#837902", "untraceable_ref": "C20"},
)

EDGE_PROOFS: dict[str, str] = {
    "C06": "core/pipeline/queue.py:98, 112-123 · _block_queue :860, 927",
}

CORRESPONDENCE_CASCADE: dict[str, tuple[str, ...]] = {
    "C06": ("ADB1", "DL1", "DL2", "RNG1", "JF1"),
}


def line_number(line_name: str) -> LineNumber:
    return LINE_NUMBERS.get(line_name, 12)


def visible_edge(edge_id: str) -> MetroEdge | None:
    edge = EDGE_BY_ID.get(edge_id)
    if edge is None or edge_id not in VISIBLE_EDGE_IDS:
        return None
    if edge.origin not in VISIBLE_STATION_IDS and edge.dest not in VISIBLE_STATION_IDS:
        return None
    return edge


def graph_payload() -> dict[str, Any]:
    stations = []
    for station_id in sorted(VISIBLE_STATION_IDS, key=lambda s: (STATIONS[s][0], s)):
        line_name, label = STATIONS[station_id]
        pos = STATION_POSITIONS.get(station_id)
        if pos is None:
            continue
        label_layout = STATION_LABEL_LAYOUT.get(station_id, {})
        stations.append(
            {
                "id": station_id,
                "line": line_name,
                "line_number": line_number(line_name),
                "label": label,
                "color": LINE_COLORS.get(line_name, "#8D8D8D"),
                "x": pos[0],
                "y": pos[1],
                "label_dy": label_layout.get("label_dy", -18),
                "label_size": label_layout.get("label_size", 12),
                "label_anchor": label_layout.get("label_anchor", "middle"),
                "label_dx": label_layout.get("label_dx", 0),
                "instrumented": station_id in {
                    "SRC1",
                    "SRC8",
                    "ADB2",
                    "ADB3",
                    "DL1",
                    "RNG1",
                    "RNG5",
                    "RUC2",
                    "RUC3",
                    "JF1",
                    "JF2",
                    "JF4",
                    "SUI3",
                    "SUI4",
                    "SUI6",
                    "SOC1",
                },
            }
        )
    edges = []
    for edge_id in VISIBLE_EDGE_IDS:
        edge = EDGE_BY_ID[edge_id]
        origin = _map_station(edge.origin)
        dest = _map_station(edge.dest)
        if origin is None or dest is None:
            continue
        edges.append(
            {
                "id": edge.edge_id,
                "origin": origin,
                "dest": dest,
                "kind": edge.kind,
                "untraceable": edge.edge_id in UNTRACEABLE_EDGES,
                "proof": EDGE_PROOFS.get(edge.edge_id),
            }
        )
    lines = [
        {
            "number": num,
            "name": name,
            "color": LINE_COLORS.get(name, "#8D8D8D"),
            "x": LEVEL1_POSITIONS[name][0],
            "y": LEVEL1_POSITIONS[name][1],
            "focus_station_id": LINE_FOCUS_STATION.get(name),
        }
        for num, name in LINE_ORDER
    ]
    return {
        "view_box": {"width": 1000, "height": 800},
        "station_count_total": len(STATIONS),
        "station_count_visible": len(VISIBLE_STATION_IDS),
        "edge_count_visible": len(edges),
        "untraceable_count": len(UNTRACEABLE_EDGES),
        "lines": lines,
        "stations": stations,
        "edges": edges,
        "level1_edges": [],
        "diagnostics": [],
        "rng5_mechanisms": list(RNG5_MECHANISMS),
        "rng5_exits": list(RNG5_EXITS),
        "peer_off_plan": {"x": 890, "y": 640, "label": "Pair (hors plan)"},
        "ruche_ring": {"cx": 800, "cy": 465, "r": 75},
        "retention_days": 7,
    }