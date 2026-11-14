"""Wawacity HTTP redirect policy (audit S3d #10).

Only follow redirects to HTTPS hosts on the Wawacity brand. SSRF hooks still run on
each outbound request; this module validates redirect targets before httpx follows them.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

import httpx

from mediatracker.security.ssrf import OutboundUrlRejected, enforce_outbound_request_url

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_BRAND_PREFIX = "wawacity"


class WawacityRedirectRejected(Exception):
    """Redirect target refused before any request to that URL."""

    def __init__(self, message: str, *, target_host: str | None = None) -> None:
        super().__init__(message)
        self.target_host = target_host


def is_wawacity_brand_host(host: str) -> bool:
    """True when the registrable domain's primary label starts with ``wawacity``."""
    label = _registrable_primary_label(host)
    return label.startswith(_BRAND_PREFIX)


def validate_wawacity_redirect_target(
    target_url: str,
    *,
    from_url: str,
    configured_base: str,
) -> str:
    """Static checks on a redirect Location before following it."""
    _ = configured_base
    text = target_url.strip()
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    from_scheme = urlparse(from_url).scheme.lower()
    if from_scheme == "https" and scheme == "http":
        host = _host_from_parsed(parsed)
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : passage de HTTPS vers HTTP interdit.",
            target_host=host or None,
        )
    if scheme != "https":
        host = _host_from_parsed(parsed)
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : la cible doit utiliser HTTPS.",
            target_host=host or None,
        )
    if parsed.username or parsed.password:
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : l'URL cible ne doit pas contenir d'identifiant.",
            target_host=_host_from_parsed(parsed) or None,
        )
    if parsed.port is not None and parsed.port != 443:
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : port non standard.",
            target_host=_host_from_parsed(parsed) or None,
        )
    host = _host_from_parsed(parsed)
    if not host:
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : nom d'hôte invalide.",
            target_host=None,
        )
    if not _is_domain_hostname(host):
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : les adresses IP ne sont pas acceptées.",
            target_host=host,
        )
    if not is_wawacity_brand_host(host):
        raise WawacityRedirectRejected(
            "Redirection Wawacity refusée : le domaine cible n'appartient pas à la marque.",
            target_host=host,
        )
    try:
        enforce_outbound_request_url(text)
    except OutboundUrlRejected as exc:
        raise WawacityRedirectRejected(str(exc), target_host=host) from exc
    return text


def get_following_brand_redirects(
    client: httpx.Client,
    url: str,
    *,
    configured_base: str,
    max_redirects: int = 5,
) -> httpx.Response:
    """GET with manual redirects; refused targets never receive a request."""
    history: list[httpx.Response] = []
    current = url
    for _hop in range(max_redirects + 1):
        response = client.get(current, follow_redirects=False)
        if response.status_code not in _REDIRECT_CODES:
            response.history = list(history)
            return response
        location = response.headers.get("location")
        if not location:
            raise WawacityRedirectRejected(
                "Redirection Wawacity sans en-tête Location.",
                target_host=None,
            )
        next_url = urljoin(str(response.url), location)
        validate_wawacity_redirect_target(
            next_url,
            from_url=str(response.url),
            configured_base=configured_base,
        )
        if response.status_code == 303:
            pass
        history.append(response)
        current = next_url
    raise WawacityRedirectRejected(
        "Redirection Wawacity refusée : trop de redirections.",
        target_host=None,
    )


def record_redirect_blocked(db, *, target_host: str | None) -> None:
    from mediatracker.events import record_event

    host = target_host or "inconnu"
    record_event(
        db,
        "source.redirect_blocked",
        payload={"host": host, "source": "wawacity"},
        dedupe_key=f"wawacity.redirect_blocked:{host}",
    )


def _registrable_primary_label(host: str) -> str:
    lowered = host.casefold().rstrip(".")
    if lowered.startswith("www."):
        lowered = lowered[4:]
    parts = [part for part in lowered.split(".") if part]
    if len(parts) < 2:
        return parts[0] if parts else ""
    return parts[-2]


def _host_from_parsed(parsed) -> str:
    host = (parsed.hostname or "").lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _is_domain_hostname(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return False
    if host.endswith(".local"):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return False
