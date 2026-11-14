"""Mesures légères de charge système pour adapter le rythme du démon empreintes."""

from __future__ import annotations

import os
import re
from pathlib import Path

_PRESSURE_RE = re.compile(r"avg10=([\d.]+)")


def _loadavg_pressure() -> float | None:
    try:
        load1, _, _ = os.getloadavg()
    except (AttributeError, OSError):
        return None
    cpus = os.cpu_count() or 1
    return min(1.0, float(load1) / float(max(1, cpus)))


def _proc_pressure(path: str) -> float | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    scores: list[float] = []
    for line in text.splitlines():
        if not line.startswith("some "):
            continue
        match = _PRESSURE_RE.search(line)
        if match is None:
            continue
        scores.append(min(1.0, float(match.group(1)) / 100.0))
    if not scores:
        return None
    return max(scores)


def system_pressure_score() -> float | None:
    """Score 0–1 (plus haut = machine plus sollicitée), ou ``None`` si indisponible."""
    parts: list[float] = []
    load = _loadavg_pressure()
    if load is not None:
        parts.append(load)
    for proc in ("/proc/pressure/cpu", "/proc/pressure/io"):
        score = _proc_pressure(proc)
        if score is not None:
            parts.append(score)
    if not parts:
        return None
    return max(parts)
