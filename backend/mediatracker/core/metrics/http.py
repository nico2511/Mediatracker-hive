"""Per-route HTTP latency aggregates without storing individual requests."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Millisecond bucket upper bounds (last bucket catches the rest).
_MS_BUCKETS = (5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0)


@dataclass
class RouteAggregate:
    count: int = 0
    sum_ms: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(_MS_BUCKETS) + 1))

    def record(self, duration_ms: float) -> None:
        self.count += 1
        self.sum_ms += duration_ms
        for index, upper in enumerate(_MS_BUCKETS):
            if duration_ms <= upper:
                self.buckets[index] += 1
                return
        self.buckets[-1] += 1

    def mean_ms(self) -> float | None:
        if not self.count:
            return None
        return round(self.sum_ms / self.count, 2)

    def approx_p95_ms(self) -> float | None:
        if not self.count:
            return None
        target = max(1, int(self.count * 0.95 + 0.9999))
        seen = 0
        for index, upper in enumerate(_MS_BUCKETS):
            seen += self.buckets[index]
            if seen >= target:
                return upper
        return _MS_BUCKETS[-1] * 2

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "count": self.count,
            "mean_ms": self.mean_ms(),
            "p95_ms": self.approx_p95_ms(),
        }


class HttpMetricsRegistry:
    def __init__(self) -> None:
        self._routes: dict[str, RouteAggregate] = {}

    def record(self, route: str, duration_ms: float) -> None:
        agg = self._routes.get(route)
        if agg is None:
            agg = RouteAggregate()
            self._routes[route] = agg
        agg.record(duration_ms)

    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {route: agg.to_dict() for route, agg in sorted(self._routes.items())}

    def reset(self) -> None:
        self._routes.clear()


def route_key(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return request.url.path


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, registry: HttpMetricsRegistry, *, on_request_end=None) -> None:
        super().__init__(app)
        self._registry = registry
        self._on_request_end = on_request_end

    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000.0
            key = route_key(request)
            self._registry.record(key, duration_ms)
            if self._on_request_end is not None:
                self._on_request_end()
            raise
        duration_ms = (time.perf_counter() - started) * 1000.0
        key = route_key(request)
        self._registry.record(key, duration_ms)
        if self._on_request_end is not None:
            self._on_request_end()
        return response
