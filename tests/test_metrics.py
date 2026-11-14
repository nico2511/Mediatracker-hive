"""Collecteur de métriques : /proc simulé, tampon, middleware HTTP."""

import time
import tracemalloc
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from tests.conftest import login

from mediatracker.core.metrics.buffer import CircularSeries
from mediatracker.core.metrics.collector import MetricsCollector
from mediatracker.core.metrics.http import HttpMetricsMiddleware, HttpMetricsRegistry, RouteAggregate
from mediatracker.core.metrics.proc import (
    ProcessMetrics,
    ProcTimes,
    cpu_percent_between,
    find_pid_by_cmdline,
    read_process_times,
    read_rss_bytes,
    read_system_jiffies,
    read_write_bytes,
    resolve_role_pids,
    sample_process,
)


def _fake_proc(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    api = root / "100"
    api.mkdir()
    api_stat = (
        "100 (api) S 0 0 0 0 0 0 0 0 0 0 1000 2000 0 0 0 0 0 0 0 0 512"
        " 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    )
    (api / "stat").write_text(api_stat, encoding="utf-8")
    (api / "io").write_text("read_bytes: 10\nwrite_bytes: 4096\n", encoding="utf-8")
    worker = root / "200"
    worker.mkdir()
    (worker / "cmdline").write_bytes(b"python\x00-m\x00mediatracker.worker\x00")
    worker_stat = (
        "200 (worker) S 0 0 0 0 0 0 0 0 0 0 2000 4000 0 0 0 0 0 0 0 0 1024"
        " 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    )
    (worker / "stat").write_text(worker_stat, encoding="utf-8")
    (worker / "io").write_text("write_bytes: 8192\n", encoding="utf-8")
    (root / "stat").write_text(
        "cpu 100 10 20 30 40 50 60 70 80\n"
        "cpu0 10 1 2 3 4 5 6 7\n"
        "intr 0\n",
        encoding="utf-8",
    )
    return root


def test_proc_parsing_and_cpu_delta(tmp_path: Path):
    root = _fake_proc(tmp_path)
    times = read_process_times(root, 100)
    assert times == ProcTimes(utime=1000, stime=2000)
    assert read_rss_bytes(root, 100) == 512 * 4096
    assert read_write_bytes(root, 100) == 4096
    jiffies = read_system_jiffies(root)
    assert jiffies == 100 + 10 + 20 + 30 + 40 + 50 + 60
    pct = cpu_percent_between(
        ProcTimes(900, 1800),
        jiffies - 100,
        times,
        jiffies,
        cpu_count=2,
    )
    assert pct is not None
    assert pct > 0
    assert find_pid_by_cmdline(root, "mediatracker.worker") == 200
    roles = resolve_role_pids(root, 100)
    assert roles["api"] == 100
    assert roles["worker"] == 200
    assert roles["aria2"] is None
    metrics, _ = sample_process(root, 999, prev_times=None, prev_jiffies=None, cur_jiffies=jiffies, cpu_count=1)
    assert metrics == ProcessMetrics(pid=999, cpu_percent=None, rss_bytes=None, write_bytes=None)


def test_proc_missing_files_return_nulls(tmp_path: Path):
    root = tmp_path / "empty_proc"
    root.mkdir()
    assert read_process_times(root, 1) is None
    assert read_rss_bytes(root, 1) is None
    assert read_write_bytes(root, 1) is None
    assert read_system_jiffies(root) is None


def test_circular_series_keeps_order_and_cap():
    series = CircularSeries(3)
    for index in range(5):
        series.append(float(index), {"n": index})
    snap = series.snapshot()
    assert len(snap) == 3
    assert [row["n"] for row in snap] == [2, 3, 4]


def test_route_aggregate_p95_approx():
    agg = RouteAggregate()
    for _ in range(100):
        agg.record(3.0)
    for _ in range(5):
        agg.record(200.0)
    assert agg.count == 105
    assert agg.mean_ms() == pytest.approx(12.38, abs=0.1)
    assert agg.approx_p95_ms() == 5.0


def test_http_middleware_records_route():
    registry = HttpMetricsRegistry()

    async def home(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", home)])
    app.add_middleware(HttpMetricsMiddleware, registry=registry)
    client = TestClient(app)
    client.get("/")
    client.get("/")
    snap = registry.snapshot()
    assert snap["/"]["count"] == 2
    assert snap["/"]["mean_ms"] is not None


def test_metrics_endpoint_requires_auth(client):
    hidden = client.get("/api/v1/health/metrics")
    assert hidden.status_code == 401
    login(client)
    body = client.get("/api/v1/health/metrics")
    assert body.status_code == 200, body.text
    payload = body.json()
    assert "instant" in payload
    assert "series" in payload
    assert "http" in payload
    assert set(payload["instant"]["processes"]) == {"api", "worker", "aria2"}


def test_collector_benchmark_documented(tmp_path: Path):
    """Référence PR : 500 lectures /proc simulées — pic mémoire < 10 Mo, < 2 s."""
    root = _fake_proc(tmp_path)
    collector = MetricsCollector()
    collector._proc_root = lambda: root
    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(500):
        collector._build_point(None, None)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    series = CircularSeries(maxlen=181)
    for index in range(500):
        series.append(float(index), {"i": index})
    assert len(series) == 181
    assert elapsed < 2.0
    assert peak < 10 * 1024 * 1024
