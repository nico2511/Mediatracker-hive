"""Integration tests against a real aria2c binary (skipped when aria2c is not installed)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.aria2_live import (
    LiveAria2,
    list_incomplete_files,
    live_aria2,
    require_aria2c,
    tell_queue,
    wait_until,
)

pytestmark = pytest.mark.usefixtures("ssrf_real_dns")


class _FixtureHttpServer:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> None:
        payload = self.payload

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args) -> None:
                return

            def do_GET(self) -> None:
                if self.path == "/missing":
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/payload":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(404)
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def test_aria2_live_stale_session_entry_not_reloaded_after_bootstrap_filter(tmp_path):
    """Session left from failed jobs must be filtered before aria2 starts (no relaunch, no new partials)."""
    require_aria2c()
    from mediatracker.providers.downloaders.aria2 import filter_aria2_session_file

    payload = b"stale-session-" + bytes(range(64)) * 4
    http = _FixtureHttpServer(payload)
    http.start()
    daemon = LiveAria2(tmp_path / "stale-session")
    session_text = "\n".join(
        [
            f"{http.base_url}/missing",
            "  gid=stale-error",
            "  out=Stale.Line.S01E01.mkv",
            "",
        ]
    )
    try:
        daemon.config_dir.mkdir(parents=True, exist_ok=True)
        daemon.incomplete_dir.mkdir(parents=True, exist_ok=True)
        leftover = daemon.incomplete_dir / "Stale.Line.S01E01.mkv"
        leftover.write_bytes(b"leftover-partial")
        daemon.session_path.write_text(session_text, encoding="utf-8")
        dropped = filter_aria2_session_file(daemon.session_path, keep_gids=set(), keep_names=set())
        assert dropped == 1

        client = daemon.start()
        assert tell_queue(client, "aria2.tellActive") == []
        assert tell_queue(client, "aria2.tellWaiting") == []
        assert tell_queue(client, "aria2.tellStopped") == []
        names = list_incomplete_files(daemon.incomplete_dir)
        assert names == {leftover.name}
        assert not any(".aria2" in name or ".1." in name for name in names)
        daemon.stop()
    finally:
        http.stop()


def test_aria2_live_404_not_requeued_after_restart(tmp_path):
    require_aria2c()
    payload = b"ridge-sample-" + bytes(range(256)) * 8
    http = _FixtureHttpServer(payload)
    http.start()
    daemon = LiveAria2(tmp_path / "a404")
    try:
        client = daemon.start()
        gid = client.add_uri(f"{http.base_url}/missing", daemon.incomplete_dir, "Ridge.Line.S01E01.mkv")
        status = wait_until(client, gid, "error", timeout=90)
        assert status.state == "error"
        before = list_incomplete_files(daemon.incomplete_dir)
        daemon.stop()

        client = daemon.start()
        assert tell_queue(client, "aria2.tellWaiting") == []
        assert tell_queue(client, "aria2.tellStopped") == []
        assert list_incomplete_files(daemon.incomplete_dir) == before
        daemon.stop()
    finally:
        http.stop()


def test_aria2_live_no_numeric_suffix_when_target_exists(tmp_path):
    require_aria2c()
    payload = b"cedar-payload-" + bytes(range(200)) * 6
    http = _FixtureHttpServer(payload)
    http.start()
    target_name = "Cedar.Post.S02E03.mkv"
    try:
        with live_aria2(tmp_path / "exists") as (daemon, client):
            existing = daemon.incomplete_dir / target_name
            existing.write_bytes(b"already-there")
            gid = client.add_uri(f"{http.base_url}/payload", daemon.incomplete_dir, target_name)
            status = wait_until(client, gid, "complete", "error", timeout=90)
            assert status.state == "complete"
            names = {path.name for path in daemon.incomplete_dir.iterdir()}
            assert f"{Path(target_name).stem}.1{Path(target_name).suffix}" not in names
            assert not any(".1." in name for name in names)
    finally:
        http.stop()


def test_aria2_live_valid_download_size_matches_payload(tmp_path):
    require_aria2c()
    payload = b"north-harbor-" + bytes(range(128)) * 10
    http = _FixtureHttpServer(payload)
    http.start()
    target_name = "North.Harbor.S01E05.mkv"
    try:
        with live_aria2(tmp_path / "ok") as (daemon, client):
            gid = client.add_uri(f"{http.base_url}/payload", daemon.incomplete_dir, target_name)
            status = wait_until(client, gid, "complete", timeout=90)
            assert status.state == "complete"
            target = daemon.incomplete_dir / target_name
            assert target.is_file()
            assert target.stat().st_size == len(payload)
    finally:
        http.stop()
