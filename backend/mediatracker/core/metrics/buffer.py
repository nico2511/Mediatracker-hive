"""Fixed-size in-memory time series (no disk, no background thread)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SeriesPoint:
    ts: float
    payload: dict[str, Any]


class CircularSeries:
    def __init__(self, maxlen: int) -> None:
        self._points: deque[SeriesPoint] = deque(maxlen=maxlen)

    def append(self, ts: float, payload: dict[str, Any]) -> None:
        self._points.append(SeriesPoint(ts=ts, payload=payload))

    def snapshot(self) -> list[dict[str, Any]]:
        return [{"ts": point.ts, **point.payload} for point in self._points]

    def __len__(self) -> int:
        return len(self._points)
