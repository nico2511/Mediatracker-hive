"""SSRF policy for user-configurable outbound URLs (audit S3c)."""

import ipaddress
import socket
from typing import Any

import httpx
import pytest

from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError
from mediatracker.security.ssrf import (
    OutboundUrlRejected,
    check_resolved_host,
    enforce_outbound_request_url,
    is_aria2_loopback_rpc,
    settings_rejection_message,
    validate_outbound_http_url,
    validate_outbound_url_static,
)


def _resolve_to(*addresses: str):
    def resolver(host: str, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0):
        del host, port, family, type, proto, flags
        rows = []
        for addr in addresses:
            ip = ipaddress.ip_address(addr)
            if isinstance(ip, ipaddress.IPv4Address):
                rows.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (str(ip), 0)))
            else:
                rows.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (str(ip), 0, 0, 0)))
        return rows

    return resolver


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/",
        "http://127.8.0.1/",
        "http://[::1]/api",
        "http://169.254.1.2/",
        "http://[fe80::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[fd00:ec2::254]/",
        "http://metadata.google.internal/hook",
    ],
)
def test_blocks_loopback_link_local_and_metadata(url: str):
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_http_url(url, resolve=_resolve_to("127.0.0.1"))
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_http_url(url)


@pytest.mark.parametrize(
    ("url", "addrs"),
    [
        ("http://svc-lan.example/", ("10.0.0.50",)),
        ("http://nas-movies.example:8096/", ("172.16.12.3",)),
        ("https://wawa-fictif.example/", ("192.168.0.44",)),
        ("http://nas-v6.example/", ("fd12:3456:789a:1::9",)),
    ],
)
def test_allows_rfc1918_and_ula_when_resolved(url: str, addrs: tuple[str, ...]):
    assert validate_outbound_http_url(url, resolve=_resolve_to(*addrs)) == url


def test_blocks_when_dns_points_to_loopback():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_http_url("http://evil-fictif.example/", resolve=_resolve_to("127.0.0.1"))


def test_blocks_when_dns_points_to_metadata():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_http_url("http://rebinder-fictif.example/", resolve=_resolve_to("169.254.169.254"))


def test_rejects_non_http_schemes():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_http_url("file:///etc/passwd", resolve=_resolve_to("192.168.1.1"))


def test_aria2_loopback_exception():
    url = "http://127.0.0.1:6800/jsonrpc"
    assert is_aria2_loopback_rpc(url)
    assert validate_outbound_http_url(url, allow_aria2_loopback=True) == url
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_http_url(url, allow_aria2_loopback=False)


def test_settings_message_french():
    assert "link-local" in settings_rejection_message()
    assert "métadonnées" in settings_rejection_message()


def test_dns_failure_is_not_security_rejection():
    def broken(_host, _port, family=0, type=0, proto=0, flags=0):
        del family, type, proto, flags
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    check_resolved_host("conteneur-arrete-fictif.example", resolve=broken)
    assert enforce_outbound_request_url("http://conteneur-arrete-fictif.example/", resolve=broken)


def test_jellyfin_client_defers_dns_to_request(monkeypatch):
    calls: list[str] = []

    def track(host, port, family=0, type=0, proto=0, flags=0):
        del port, family, type, proto, flags
        calls.append(host)
        raise socket.gaierror(socket.EAI_NONAME, "fail")

    monkeypatch.setattr("mediatracker.security.ssrf.socket.getaddrinfo", track)
    client = JellyfinClient(
        "http://jellyfin-arrete-fictif.example:8096",
        "cle-fictive",
        None,
        transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("refused"))),
    )
    assert calls == []
    with pytest.raises(JellyfinError) as exc_info:
        client.system_info()
    assert exc_info.value.code == "TRANSPORT"
    assert calls == ["jellyfin-arrete-fictif.example"]


def test_settings_api_accepts_unresolvable_docker_host(client, monkeypatch):
    from tests.conftest import csrf, login

    def fail_dns(*_args, **_kwargs):
        raise AssertionError("DNS ne doit pas être appelé à l'enregistrement des réglages")

    monkeypatch.setattr("mediatracker.security.ssrf.socket.getaddrinfo", fail_dns)
    login(client)
    response = client.patch(
        "/api/v1/settings",
        json={"values": {"jellyfin_url": "http://jellyfin-arrete-fictif.example:8096"}},
        headers=csrf(client),
    )
    assert response.status_code == 200


def test_static_blocks_literal_loopback_without_dns():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_url_static("http://127.0.0.1:8096")


def test_settings_api_rejects_loopback_jellyfin(client):
    from tests.conftest import csrf, login

    login(client)
    response = client.patch(
        "/api/v1/settings",
        json={"values": {"jellyfin_url": "http://127.0.0.1:8096"}},
        headers=csrf(client),
    )
    assert response.status_code == 422
    assert settings_rejection_message() in response.json()["detail"]
