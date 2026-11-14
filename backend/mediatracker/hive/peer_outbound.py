"""Politique réseau des requêtes HTTP pair Hive (SSRF, redirections)."""

from __future__ import annotations

from mediatracker.security.ssrf import (
    OutboundUrlRejected,
    enforce_outbound_request_url,
    validate_outbound_url_static,
)


def validate_hive_peer_base_url(base_url: str) -> str:
    text = base_url.strip().rstrip("/")
    if not text:
        raise OutboundUrlRejected("URL vide.")
    return validate_outbound_url_static(text)


def enforce_hive_peer_request_url(base_url: str, url_path: str) -> str:
    base = base_url.strip().rstrip("/")
    path = url_path if url_path.startswith("/") else f"/{url_path}"
    return enforce_outbound_request_url(f"{base}{path}")
