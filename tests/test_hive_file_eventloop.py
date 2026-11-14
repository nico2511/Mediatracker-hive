"""Regression : GET /hive/files ne doit pas bloquer la boucle asyncio (healthz concurrent)."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from tests.test_hive_t3_peer_http import _enable, _settings_pair
from tests.test_hive_t5 import URL_A, _join_ruche, _seed_file_on_a

from mediatracker.api.app import create_app
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.security.logging import UVICORN_LOG_CONFIG_SKIP

# Paramètres figés (reproductibles, alignés sur l'artifact de repro).
FILE_BYTES = 8 * 1024 * 1024
DELAY_PER_READ_S = 0.09
HEALTHZ_BUDGET_S = 0.35
HIVE_MAX_ELAPSED_S = 3.5
PROBE_INTERVAL_S = 0.05
MIN_PROBES_DURING_SLOW_READ = 3
SERVER_STOP_JOIN_S = 15.0
PROBE_CLIENT_TIMEOUT_S = HEALTHZ_BUDGET_S + 0.5

_orig_path_open = Path.open


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def transfer_pair(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "evt_a"))
    app_b = create_app(_settings_pair(tmp_path, "evt_b"))
    for app in (app_a, app_b):
        app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        app.state.hive_send_bandwidth = TransferBandwidthLimiter(bps=0)
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()


def _patch_slow_reads(monkeypatch: pytest.MonkeyPatch, target: Path, delay_s: float, state: dict) -> None:
    def _patched_open(self, mode="r", *args, **kwargs):
        handle = _orig_path_open(self, mode, *args, **kwargs)
        if "b" not in mode or delay_s <= 0:
            return handle
        try:
            if not self.resolve().samefile(target):
                return handle
        except OSError:
            return handle
        orig_read = handle.read

        def delayed_read(size=-1):
            state["reads"] += 1
            if state["reads"] == 1:
                state["event"].set()
            chunk = 65536
            if size < 0:
                size = chunk
            out = bytearray()
            while len(out) < size:
                take = min(chunk, size - len(out))
                time.sleep(delay_s)
                block = orig_read(take)
                if not block:
                    break
                out.extend(block)
            return bytes(out)

        handle.read = delayed_read  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(Path, "open", _patched_open)


def _probe_healthz(port: int, timeout: float) -> tuple[float, bool]:
    url = f"http://127.0.0.1:{port}/healthz"
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
        return time.monotonic() - t0, True
    except Exception:
        return time.monotonic() - t0, False


def _hive_get(port: int, path: str, headers: dict, timeout: float) -> tuple[float, int | None]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers, method="GET")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(128)
        return time.monotonic() - t0, resp.status
    except urllib.error.HTTPError as exc:
        return time.monotonic() - t0, exc.code


@pytest.fixture
def hive_file_server(transfer_pair):
    app_a, app_b = transfer_pair
    app = app_a
    port = _free_tcp_port()
    payload = os.urandom(FILE_BYTES)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app, rid, payload)
        db = app.state.session_factory()
        try:
            from mediatracker.core.storage import storage_layout

            layout = storage_layout(db, app.state.settings)
        finally:
            db.close()
        video = layout.movies_dir / "Demo (2020) [tmdbid-9001]" / "demo.bin"
    pk = load_private_key(app.state.settings)
    path = f"/hive/files/{fid}"
    headers = sign_request_headers(private_key=pk, method="GET", path=path, query="", ruche_id=rid, body=b"")
    headers["Range"] = "bytes=0-1572863"

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=UVICORN_LOG_CONFIG_SKIP,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="hive-eventloop-test")
    thread.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        _, ok = _probe_healthz(port, 2.0)
        if ok:
            break
        time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=SERVER_STOP_JOIN_S)
        pytest.fail("uvicorn did not become ready")

    try:
        yield {
            "port": port,
            "path": path,
            "headers": headers,
            "video": video.resolve(),
        }
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_STOP_JOIN_S)
        if thread.is_alive():
            pytest.fail("uvicorn server did not stop within join budget")


def test_healthz_stays_reactive_during_slow_hive_file_read(hive_file_server, monkeypatch):
    def _forbid_full_file_digest(*_a, **_k):
        raise AssertionError("chunk_digests_for_file must not run on GET /hive/files path")

    monkeypatch.setattr("mediatracker.hive.chunk_digest.chunk_digests_for_file", _forbid_full_file_digest)
    port = hive_file_server["port"]
    path = hive_file_server["path"]
    headers = hive_file_server["headers"]
    video: Path = hive_file_server["video"]

    slow_state = {"reads": 0, "event": threading.Event()}
    _patch_slow_reads(monkeypatch, video, DELAY_PER_READ_S, slow_state)
    probe_latencies: list[float] = []
    hive_done = threading.Event()
    hive_holder: dict = {}

    def _watchdog():
        if not slow_state["event"].wait(timeout=90):
            probe_latencies.append(-1.0)
            return
        while not hive_done.is_set():
            lat, ok = _probe_healthz(port, PROBE_CLIENT_TIMEOUT_S)
            if not ok:
                probe_latencies.append(-1.0)
                return
            probe_latencies.append(lat)
            time.sleep(PROBE_INTERVAL_S)

    def _hive():
        try:
            elapsed, status = _hive_get(port, path, headers, timeout=180.0)
            hive_holder["elapsed_s"] = elapsed
            hive_holder["status"] = status
        finally:
            hive_done.set()

    watch_t = threading.Thread(target=_watchdog, daemon=True)
    hive_t = threading.Thread(target=_hive, daemon=True)
    hive_t.start()
    watch_t.start()
    hive_t.join(timeout=200)
    watch_t.join(timeout=5)

    assert hive_holder.get("status") == 206, hive_holder
    assert hive_holder["elapsed_s"] < HIVE_MAX_ELAPSED_S, hive_holder
    assert slow_state["reads"] >= 1, slow_state
    assert slow_state["event"].is_set(), "slow disk reads never started"
    assert probe_latencies, "no healthz probes recorded"
    assert all(lat >= 0 for lat in probe_latencies), probe_latencies
    assert len(probe_latencies) >= MIN_PROBES_DURING_SLOW_READ, probe_latencies
    worst = max(probe_latencies)
    assert worst <= HEALTHZ_BUDGET_S, json.dumps(
        {
            "worst_healthz_s": worst,
            "hive_elapsed_s": hive_holder["elapsed_s"],
            "disk_reads": slow_state["reads"],
            "samples": len(probe_latencies),
        }
    )


def test_baseline_healthz_alone_is_fast(hive_file_server):
    lat, ok = _probe_healthz(hive_file_server["port"], 2.0)
    assert ok
    assert lat < 0.2
