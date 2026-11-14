"""Wawacity domain sync from scan redirects.

Detection uses redirect history from requests the poller already makes.
By default, a validated redirect is applied automatically after the same
static checks as mode Proposer plus a single homepage probe (as on manual apply).
Set ``wawacity_domain_propose_only`` to keep proposal-only behaviour.

After an admin revert, the host that was left is stored in
``wawacity_domain_auto_blocked_host`` so automatic migration will not switch
back to it until an admin explicitly applies that domain again (POST /apply).
"""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from mediatracker.db.models import Setting
from mediatracker.events import record_event
from mediatracker.providers.sources.loading import apply_source_base_urls, source_base_url
from mediatracker.providers.sources.manual import _HOSTERS
from mediatracker.providers.sources.wawacity import DEFAULT_USER_AGENT
from mediatracker.security.ssrf import OutboundUrlRejected, httpx_request_event_hooks, validate_outbound_url_static
from mediatracker.security.wawacity_redirect import (
    WawacityRedirectRejected,
    get_following_brand_redirects,
    is_wawacity_brand_host,
)

logger = logging.getLogger(__name__)

CANDIDATE_KEY = "wawacity_domain_candidate"
PREVIOUS_KEY = "wawacity_previous_base_url"
PROPOSE_ONLY_KEY = "wawacity_domain_propose_only"
AUTO_BLOCKED_HOST_KEY = "wawacity_domain_auto_blocked_host"
LAST_MIGRATION_KEY = "wawacity_domain_last_migration"
_REDIRECT_CODES = frozenset({301, 302, 308})
_FILE_HOST_SUFFIXES = frozenset({host for host, _name in _HOSTERS})
_AUTO_REASON = "redirection constatée lors d'une requête Wawacity"


class DomainValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def read_domain_state(db: Session, settings) -> dict[str, str | None]:
    current = source_base_url(db, settings, "wawacity") or None
    candidate = _text(db, CANDIDATE_KEY) or None
    previous = _text(db, PREVIOUS_KEY) or None
    return {"current": current, "candidate": candidate, "previous": previous}


def maybe_propose_from_redirect(
    db: Session,
    settings,
    request_url: str,
    response: httpx.Response,
    *,
    configured_base: str,
    transport: httpx.BaseTransport | None = None,
    client_factory: Callable[..., httpx.Client] | None = None,
) -> None:
    """Observe a redirect during a normal Wawacity GET and propose or auto-apply."""
    handle_domain_from_redirect(
        db,
        settings,
        request_url,
        response,
        configured_base=configured_base,
        transport=transport,
        client_factory=client_factory,
    )


def handle_domain_from_redirect(
    db: Session,
    settings,
    request_url: str,
    response: httpx.Response,
    *,
    configured_base: str,
    transport: httpx.BaseTransport | None = None,
    client_factory: Callable[..., httpx.Client] | None = None,
) -> None:
    candidate = redirect_candidate_base(request_url, response, configured_base)
    if candidate is None:
        return
    current = source_base_url(db, settings, "wawacity").rstrip("/")
    normalized = candidate.rstrip("/")
    if normalized == current:
        return
    try:
        static_validate_candidate(normalized)
    except DomainValidationError:
        return
    candidate_host = _host(normalized)
    if candidate_host and candidate_host == _text(db, AUTO_BLOCKED_HOST_KEY):
        return
    if _propose_only(db, settings):
        stored = _text(db, CANDIDATE_KEY).rstrip("/")
        if stored == normalized:
            return
        _set_text(db, CANDIDATE_KEY, normalized)
        record_event(
            db,
            "source.domain_proposed",
            payload={
                "host": candidate_host,
                "from_host": _host(configured_base),
            },
            dedupe_key=f"wawacity.domain:{candidate_host}",
        )
        db.commit()
        return
    try:
        probe_homepage(
            normalized,
            user_agent=_user_agent(db, settings),
            transport=transport,
            client_factory=client_factory,
        )
    except DomainValidationError:
        return
    _persist_domain_change(
        db,
        settings,
        normalized,
        previous=current,
        event_type="source.domain_auto_migrated",
        reason=_AUTO_REASON,
        dedupe_key=f"wawacity.domain_auto:{candidate_host}",
    )
    db.commit()


def apply_candidate(
    db: Session,
    settings,
    *,
    transport: httpx.BaseTransport | None = None,
    client_factory: Callable[..., httpx.Client] | None = None,
) -> dict[str, str | None]:
    candidate = _text(db, CANDIDATE_KEY).rstrip("/")
    if not candidate:
        raise DomainValidationError("NO_CANDIDATE", "Aucun domaine candidat.")
    static_validate_candidate(candidate)
    probe_homepage(candidate, user_agent=_user_agent(db, settings), transport=transport, client_factory=client_factory)
    current = source_base_url(db, settings, "wawacity").rstrip("/")
    _persist_domain_change(
        db,
        settings,
        candidate,
        previous=current,
        event_type="source.domain_applied",
        reason="application manuelle par un administrateur",
        dedupe_key=None,
        clear_candidate=True,
        clear_auto_block_for_host=_host(candidate),
    )
    db.commit()
    return read_domain_state(db, settings)


def revert_domain(db: Session, settings) -> dict[str, str | None]:
    previous = _text(db, PREVIOUS_KEY).rstrip("/")
    if not previous:
        raise DomainValidationError("NO_PREVIOUS", "Aucun domaine précédent enregistré.")
    current = source_base_url(db, settings, "wawacity").rstrip("/")
    rejected_host = _host(current)
    if rejected_host:
        _set_text(db, AUTO_BLOCKED_HOST_KEY, rejected_host)
    _set_text(db, "wawacity_base_url", previous)
    apply_source_base_urls(db, {"wawacity_base_url": previous})
    _set_text(db, PREVIOUS_KEY, "")
    _set_text(db, CANDIDATE_KEY, "")
    now = datetime.now(timezone.utc)
    _record_last_migration(db, from_url=current, to_url=previous, when=now)
    record_event(
        db,
        "source.domain_reverted",
        payload={
            "host": _host(previous),
            "from_host": rejected_host or None,
            "rejected_host": rejected_host or None,
            "reason": "retour administrateur ; le domaine quitté ne sera plus appliqué automatiquement",
        },
        when=now,
    )
    db.commit()
    return read_domain_state(db, settings)


def redirect_candidate_base(request_url: str, response: httpx.Response, configured_base: str) -> str | None:
    if not response.history:
        return None
    origin_host = _host(configured_base) or _host(request_url)
    if not origin_host:
        return None
    for hop in response.history:
        if hop.status_code not in _REDIRECT_CODES:
            continue
        location = hop.headers.get("location")
        if not location:
            continue
        target = urljoin(str(hop.url), location)
        target_host = _host(target)
        if not target_host or target_host == origin_host:
            continue
        base = _https_base_from_redirect_target(target)
        if base is None:
            continue
        return base
    return None


def static_validate_candidate(base_url: str) -> None:
    text = base_url.strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise DomainValidationError("HTTP", "Le candidat doit utiliser HTTPS.")
    if parsed.username or parsed.password:
        raise DomainValidationError("HOST", "Le candidat ne doit pas contenir d'identifiant.")
    if parsed.port is not None and parsed.port != 443:
        raise DomainValidationError("HOST", "Le candidat ne doit pas utiliser un port non standard.")
    host = _host(text)
    if not host:
        raise DomainValidationError("HOST", "Nom d'hôte invalide.")
    if not _is_domain_hostname(host):
        raise DomainValidationError("HOST", "Le candidat doit être un nom de domaine public.")
    if _is_file_host(host):
        raise DomainValidationError("FILE_HOST", "Les hébergeurs de fichiers ne sont pas acceptés.")
    if not _brand_match(host):
        raise DomainValidationError("BRAND", "Le domaine ne ressemble pas à Wawacity.")


def probe_homepage(
    base_url: str,
    *,
    user_agent: str,
    transport: httpx.BaseTransport | None = None,
    client_factory: Callable[..., httpx.Client] | None = None,
) -> None:
    """At most one GET on the candidate homepage; redirects must stay on the candidate host."""
    static_validate_candidate(base_url)
    try:
        validate_outbound_url_static(base_url)
    except OutboundUrlRejected as exc:
        raise DomainValidationError("HOST", str(exc)) from exc
    root = base_url.rstrip("/") + "/"
    candidate_host = _host(base_url)
    factory = client_factory or httpx.Client
    client = factory(
        transport=transport,
        timeout=20.0,
        follow_redirects=False,
        event_hooks=httpx_request_event_hooks(),
        headers={"User-Agent": user_agent, "Accept": "text/html"},
    )
    try:
        try:
            response = get_following_brand_redirects(
                client,
                root,
                configured_base=base_url,
                max_redirects=5,
            )
        except WawacityRedirectRejected as exc:
            raise DomainValidationError("REDIRECT", str(exc)) from exc
        except OutboundUrlRejected as exc:
            raise DomainValidationError("HOST", str(exc)) from exc
        except httpx.TransportError as exc:
            raise DomainValidationError("TRANSPORT", "Le candidat est injoignable.") from exc
    finally:
        client.close()
    for hop in response.history:
        location = hop.headers.get("location")
        if not location:
            continue
        target_host = _host(urljoin(str(hop.url), location))
        if target_host and target_host != candidate_host:
            raise DomainValidationError("REDIRECT", "Redirection hors du domaine candidat.")
    if _host(str(response.url)) != candidate_host:
        raise DomainValidationError("REDIRECT", "Redirection hors du domaine candidat.")
    if response.status_code >= 400:
        raise DomainValidationError("HTTP", f"Le candidat a répondu HTTP {response.status_code}.")


def _persist_domain_change(
    db: Session,
    settings,
    new_base: str,
    *,
    previous: str,
    event_type: str,
    reason: str,
    dedupe_key: str | None,
    clear_candidate: bool = True,
    clear_auto_block_for_host: str | None = None,
) -> None:
    normalized = new_base.rstrip("/")
    now = datetime.now(timezone.utc)
    if previous:
        _set_text(db, PREVIOUS_KEY, previous.rstrip("/"))
    _set_text(db, "wawacity_base_url", normalized)
    apply_source_base_urls(db, {"wawacity_base_url": normalized})
    if clear_candidate:
        _set_text(db, CANDIDATE_KEY, "")
    if clear_auto_block_for_host and clear_auto_block_for_host == _text(db, AUTO_BLOCKED_HOST_KEY):
        _set_text(db, AUTO_BLOCKED_HOST_KEY, "")
    _record_last_migration(db, from_url=previous, to_url=normalized, when=now)
    record_event(
        db,
        event_type,
        payload={
            "host": _host(normalized),
            "previous_host": _host(previous) if previous else None,
            "from_base_url": previous.rstrip("/") if previous else None,
            "to_base_url": normalized,
            "reason": reason,
            "migrated_at": now.isoformat(),
        },
        dedupe_key=dedupe_key,
        when=now,
    )


def _record_last_migration(db: Session, *, from_url: str, to_url: str, when: datetime) -> None:
    payload = {
        "from_base_url": from_url.rstrip("/") if from_url else None,
        "to_base_url": to_url.rstrip("/") if to_url else None,
        "at": when.isoformat(),
    }
    _set_text(db, LAST_MIGRATION_KEY, json.dumps(payload, separators=(",", ":")))


def _propose_only(db: Session, settings) -> bool:
    raw = _text(db, PROPOSE_ONLY_KEY)
    if raw:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(getattr(settings, "wawacity_domain_propose_only", False))


def _user_agent(db: Session, settings) -> str:
    raw = _text(db, "wawacity_user_agent") or (getattr(settings, "wawacity_user_agent", None) or "")
    text = " ".join(str(raw).split())
    return text or DEFAULT_USER_AGENT


def _brand_match(host: str) -> bool:
    return is_wawacity_brand_host(host)


def _is_file_host(host: str) -> bool:
    lowered = host.casefold()
    for suffix in _FILE_HOST_SUFFIXES:
        if lowered == suffix or lowered.endswith("." + suffix):
            return True
    return False


def _https_base_from_redirect_target(target: str) -> str | None:
    parsed = urlparse(target)
    if parsed.scheme.lower() != "https":
        return None
    if parsed.username or parsed.password:
        return None
    if parsed.port is not None and parsed.port != 443:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    host = hostname.lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not _is_domain_hostname(host):
        return None
    if not _brand_match(host):
        return None
    return f"https://{host}"


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


def _host(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _set_text(db: Session, key: str, value: str) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now
