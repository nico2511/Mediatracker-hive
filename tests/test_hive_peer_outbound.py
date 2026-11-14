"""Politique réseau du transport pair (hors routage ASGI de test)."""

from __future__ import annotations

import httpx
import pytest

from mediatracker.hive.peer_transport import peer_request, set_peer_asgi_app_for_tests
from mediatracker.security.ssrf import OutboundUrlRejected


def test_peer_request_blocks_redirect_to_loopback(monkeypatch):
    set_peer_asgi_app_for_tests(None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "http://127.0.0.1/payload-fictif"},
            request=request,
        )

    real_client = httpx.Client

    def client_with_mock(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr("mediatracker.hive.peer_transport.httpx.Client", client_with_mock)

    with pytest.raises(OutboundUrlRejected):
        peer_request(
            base_url="https://mtr.exemple.fr",
            method="GET",
            url_path="/redir-fictif",
            headers={},
            body=b"",
        )


def test_validate_peer_base_url_blocks_loopback():
    from mediatracker.hive.peer_outbound import validate_hive_peer_base_url

    with pytest.raises(OutboundUrlRejected):
        validate_hive_peer_base_url("http://127.0.0.1:8080")
