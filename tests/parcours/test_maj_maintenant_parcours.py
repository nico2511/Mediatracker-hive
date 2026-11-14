"""Parcours API : Mettre à jour maintenant via Watchtower simulé."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from tests.conftest import csrf, login

_TOKEN = "wt-demo"
_IMAGE = "ghcr.io/nico2511/mediatracker-hive"


class _FakeWatchtower:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.requests: list[dict[str, str | None]] = []
        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fake = self  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=3)


class _Handler(BaseHTTPRequestHandler):
    fake: _FakeWatchtower

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:
        fake: _FakeWatchtower = self.server.fake  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        auth = self.headers.get("Authorization")
        fake.requests.append(
            {
                "path": parsed.path,
                "image": params.get("image", [None])[0],
                "authorization": auth,
            }
        )
        if fake.mode == "401":
            self.send_response(401)
            self.end_headers()
            return
        if fake.mode == "slow":
            time.sleep(10)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def write_headers(client):
    login(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": "maj-maintenant", "scopes": ["write", "read"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _configure_watchtower(client, base_url: str) -> None:
    patched = client.patch(
        "/api/v1/settings",
        json={"values": {"watchtower_url": base_url, "watchtower_token": _TOKEN}},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text


def test_parcours_update_now_ok(client, write_headers):
    fake = _FakeWatchtower("ok")
    try:
        _configure_watchtower(client, fake.base_url)
        status = client.get("/api/v1/ha/update", headers=write_headers)
        assert status.status_code == 200, status.text
        assert status.json()["watchtower_configured"] is True

        response = client.post("/api/v1/ha/actions/update-now", headers=write_headers)
        assert response.status_code == 200, response.text
        assert len(fake.requests) == 1
        seen = fake.requests[0]
        assert seen["path"] == "/v1/update"
        assert seen["image"] == _IMAGE
        assert seen["authorization"] == f"Bearer {_TOKEN}"
    finally:
        fake.close()


def test_parcours_update_now_slow_returns_202(client, write_headers):
    fake = _FakeWatchtower("slow")
    try:
        _configure_watchtower(client, fake.base_url)
        response = client.post("/api/v1/ha/actions/update-now", headers=write_headers)
        assert response.status_code == 202, response.text
        assert len(fake.requests) == 1
    finally:
        fake.close()


def test_parcours_update_now_401(client, write_headers):
    fake = _FakeWatchtower("401")
    try:
        _configure_watchtower(client, fake.base_url)
        response = client.post("/api/v1/ha/actions/update-now", headers=write_headers)
        assert response.status_code == 502, response.text
        assert "jeton" in response.json()["detail"].casefold()
    finally:
        fake.close()


def test_parcours_update_now_not_configured(client, write_headers):
    response = client.post("/api/v1/ha/actions/update-now", headers=write_headers)
    assert response.status_code == 409, response.text
    body = client.get("/api/v1/ha/update", headers=write_headers)
    assert body.json()["watchtower_configured"] is False
