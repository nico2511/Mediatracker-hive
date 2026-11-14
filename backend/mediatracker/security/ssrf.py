"""Outbound URL policy for user-configurable HTTP targets (audit S3c).

Blocks loopback, link-local, and cloud metadata endpoints. RFC1918 and IPv6 ULA
remain allowed for LAN deployments. DNS resolution runs only at HTTP request time;
constructors and persisted settings use static checks only.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

# aria2 listens on loopback inside the same container (see docker/s6-rc.d/aria2/run).
ARIA2_LOOPBACK_RPC_PATH = "/jsonrpc"
ARIA2_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_BLOCKED_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
    }
)

_LOOPBACK = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)
_LINK_LOCAL = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
_METADATA_ADDRS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)

_SETTINGS_MESSAGE = (
    "L'adresse réseau est refusée : les boucles locales, les adresses link-local "
    "et les métadonnées cloud ne sont pas autorisées."
)
_OUTBOUND_MESSAGE = "URL de destination refusée par la politique de sécurité réseau."


class OutboundUrlRejected(ValueError):
    """Raised when an outbound URL violates the SSRF policy."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or _OUTBOUND_MESSAGE)


def settings_rejection_message() -> str:
    return _SETTINGS_MESSAGE


def is_aria2_loopback_rpc(url: str) -> bool:
    """True for the default in-container aria2 JSON-RPC endpoint."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"}:
        return False
    if (parts.path or "").rstrip("/") != ARIA2_LOOPBACK_RPC_PATH.rstrip("/"):
        return False
    host = (parts.hostname or "").lower()
    if host not in ARIA2_LOOPBACK_HOSTS:
        return False
    return not parts.username and not parts.password


def validate_outbound_url_static(
    url: str,
    *,
    allow_aria2_loopback: bool = False,
) -> str:
    """Checks that do not need DNS (safe for constructors and settings persistence)."""
    text = url.strip()
    if not text:
        raise OutboundUrlRejected("URL vide.")
    if allow_aria2_loopback and is_aria2_loopback_rpc(text):
        return text
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"}:
        raise OutboundUrlRejected("Seuls les schémas http et https sont autorisés.")
    if parts.username or parts.password:
        raise OutboundUrlRejected("L'URL ne doit pas contenir d'identifiant.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise OutboundUrlRejected("Nom d'hôte manquant.")
    if host in _BLOCKED_HOSTNAMES:
        raise OutboundUrlRejected(_OUTBOUND_MESSAGE)
    if _literal_ip_blocked(host):
        raise OutboundUrlRejected(_OUTBOUND_MESSAGE)
    return text


def check_resolved_host(
    host: str,
    *,
    resolve: Callable[[str], list[tuple[int, int, int, str, Any]]] | None = None,
) -> None:
    """Reject when DNS yields a blocked address. DNS failures are ignored (httpx handles them)."""
    resolver = resolve or socket.getaddrinfo
    try:
        infos = resolver(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return
    if not infos:
        return
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        raw = sockaddr[0]
        if raw in seen:
            continue
        seen.add(raw)
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if _ip_blocked(ip):
            raise OutboundUrlRejected(_OUTBOUND_MESSAGE)


def enforce_outbound_request_url(
    url: str,
    *,
    allow_aria2_loopback: bool = False,
    resolve: Callable[[str], list[tuple[int, int, int, str, Any]]] | None = None,
) -> str:
    """Static checks plus DNS policy. Call immediately before an outbound HTTP request."""
    text = validate_outbound_url_static(url, allow_aria2_loopback=allow_aria2_loopback)
    if allow_aria2_loopback and is_aria2_loopback_rpc(text):
        return text
    host = (urlsplit(text).hostname or "").lower().rstrip(".")
    if host and not _literal_ip_blocked(host):
        check_resolved_host(host, resolve=resolve)
    return text


def validate_outbound_http_url(
    url: str,
    *,
    allow_aria2_loopback: bool = False,
    resolve: Callable[[str], list[tuple[int, int, int, str, Any]]] | None = None,
) -> str:
    """Alias for request-time enforcement (tests and explicit pre-flight calls)."""
    return enforce_outbound_request_url(
        url,
        allow_aria2_loopback=allow_aria2_loopback,
        resolve=resolve,
    )


def httpx_request_event_hooks(*, allow_aria2_loopback: bool = False) -> dict[str, list]:
    """httpx event hooks that enforce the policy on every request (including redirects)."""

    def _on_request(request: Any) -> None:
        enforce_outbound_request_url(str(request.url), allow_aria2_loopback=allow_aria2_loopback)

    return {"request": [_on_request]}


def _literal_ip_blocked(host: str) -> bool:
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return _ip_blocked(ipaddress.ip_address(host))
    except ValueError:
        return False


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in _METADATA_ADDRS:
        return True
    for net in _LOOPBACK + _LINK_LOCAL:
        if ip in net:
            return True
    return False
