"""AllDebrid API v4 client.

Auth is an ``Authorization: Bearer`` header. The API answers HTTP 301 to
``apislow.alldebrid.com``; the client follows that redirect and resends the
original method and body. ``AUTH_BLOCKED`` (new IP, email confirmation) and
``LINK_HOST_UNAVAILABLE`` (retryable hoster outage) are explicit error codes.

Direct unlock URLs are returned to the caller and must not be stored.
"""

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from mediatracker.providers.base import AUTH_BLOCKED_MESSAGE, DebridError, DirectLink, Health, LinkCandidate

logger = logging.getLogger(__name__)

# One gap for every client in the process. The UI never calls AllDebrid itself.
_PACE_LOCK = threading.Lock()
_PACE_NEXT = 0.0
_PACE_GAP = 1.0


def _pace(sleeper: Callable[[float], None]) -> None:
    global _PACE_NEXT
    with _PACE_LOCK:
        now = time.monotonic()
        wait = _PACE_NEXT - now
        _PACE_NEXT = max(_PACE_NEXT, now) + _PACE_GAP
    if wait > 0:
        sleeper(wait)


AGENT = "mediatracker"
BASE_URL = "https://api.alldebrid.com"

# Business errors the pipeline may retry later. AUTH_BLOCKED is not among them:
# a person has to confirm the new IP from AllDebrid's email.
RETRYABLE_CODES = frozenset(
    {
        "LINK_HOST_UNAVAILABLE",
        "LINK_HOST_FULL",
        "LINK_HOST_LIMIT_REACHED",
        "LINK_TOO_MANY_DOWNLOADS",
        "LINK_TEMPORARY_UNAVAILABLE",
        "LINK_DELAYED_TIMEOUT",
        "TRANSPORT",
        "TOO_MANY_REDIRECTS",
        "NO_SERVER",
    }
)

# A dead file or an unsupported host is not a retry: the next hoster is tried.
OFFLINE_CODES = frozenset({"LINK_DOWN", "LINK_HOST_NOT_SUPPORTED"})

# Re-exported from the neutral provider contract so existing imports keep working.

# Domains used to match a normalised hoster name against /user/hosts.
HOSTER_DOMAINS: dict[str, tuple[str, ...]] = {
    "rapidgator": ("rapidgator.net", "rg.to", "rapidgator.asia"),
    "turbobit": (
        "turbobit.net",
        "turbobit.cloud",
        "turbobit.cc",
        "turbo.to",
        "turb.cc",
        "turb.pw",
        "trbbt.net",
        "trbt.cc",
        "wayupload.com",
        "htfl.net",
        "hitf.cc",
        "hitf.to",
    ),
    "nitroflare": ("nitroflare.com",),
    "katfile": ("katfile.com", "katfile.cloud"),
    "tezfiles": ("tezfiles.com",),
    "uploady": ("uploady.io",),
    "dailyuploads": ("dailyuploads.net",),
    "1fichier": ("1fichier.com",),
}


class AllDebridError(DebridError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        Exception.__init__(self, f"{code}: {message}")


class LinkInspection:
    """Result of ``/v4/link/infos`` for one hoster URL. The direct CDN URL is never here."""

    def __init__(
        self,
        *,
        link: str,
        online: bool,
        filename: str | None,
        size: int | None,
        code: str | None,
        message: str | None,
    ) -> None:
        self.link = link
        self.online = online
        self.filename = filename
        self.size = size
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


class KnownFile:
    """A file AllDebrid already generated (history) or the user pinned (saved links)."""

    def __init__(self, link: str, filename: str, size: int | None, host: str | None, source: str) -> None:
        self.link = link
        self.filename = filename
        self.size = size
        self.host = host
        self.source = source


class MagnetCacheHit:
    """Whether a magnet is already on AllDebrid's cache. ``/v4/magnet/instant`` does not
    add the torrent. Upload is a separate call, used only after this says it is cached.
    """

    def __init__(self, magnet: str, cached: bool, name: str | None) -> None:
        self.magnet = magnet
        self.cached = cached
        self.name = name


class MagnetFile:
    """One file inside a magnet AllDebrid already has."""

    def __init__(self, index: int, name: str, size: int | None, link: str | None) -> None:
        self.index = index
        self.name = name
        self.size = size
        self.link = link


class MagnetSnapshot:
    """Upload or status result. ``ready`` means the torrent is cached, not downloading."""

    def __init__(self, magnet_id: str, ready: bool, files: list[MagnetFile]) -> None:
        self.id = magnet_id
        self.ready = ready
        self.files = files


class AllDebridConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(default="", json_schema_extra={"secret": True})


class AllDebridClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        agent: str = AGENT,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        max_redirects: int = 5,
        hosts_ttl: float = 600.0,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.agent = agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_redirects = max_redirects
        self.hosts_ttl = hosts_ttl
        self._sleeper = sleeper
        from mediatracker.core.outbound_http import borrow_http_client

        self._client, self._owns_client = borrow_http_client(
            ("alldebrid", self.base_url),
            httpx.Client,
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )
        self._hosts: dict[str, dict[str, Any]] | None = None
        self._hosts_at = 0.0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_user(self) -> dict[str, Any]:
        payload = self._request("GET", "/v4/user")
        return (payload.get("data") or {}).get("user") or {}

    def pin_get(self) -> dict[str, Any]:
        """GET /v4.1/pin/get — no Bearer token."""
        return self._request_public("GET", "/v4.1/pin/get")

    def pin_check(self, pin: str, check: str) -> dict[str, Any]:
        """POST /v4/pin/check — poll until activated or expired."""
        return self._request_public(
            "POST",
            "/v4/pin/check",
            data={"pin": pin, "check": check},
        )

    def redirector(self, link: str) -> list[str]:
        """POST /v4/link/redirector with the full URL, query string included.

        Success returns virtual ``redirect.alldebrid.com`` links for /link/unlock.
        The AllDebrid error code is logged and re-raised (REDIRECTOR_NOT_SUPPORTED
        when the protector domain is unknown, REDIRECTOR_ERROR when extraction fails).
        """
        host = _public_host(link)
        try:
            payload = self._request("POST", "/v4/link/redirector", data={"link": link})
        except AllDebridError as exc:
            logger.info(
                "AllDebrid redirecteur refusé : code=%s message=%s hôte=%s.",
                exc.code,
                _scrub_message(exc.message),
                host,
            )
            raise
        links = _redirector_links(payload)
        hosts = sorted({_public_host(item) for item in links})
        logger.info("AllDebrid redirecteur : %s lien(s) hôtes=%s.", len(links), ",".join(hosts) or "-")
        return links

    def link_infos(self, links: list[str]) -> list[LinkInspection]:
        """Online check. Host links only; a redirector must be resolved first.

        AllDebrid answers HTTP 301 to ``apislow.alldebrid.com``. ``_request`` follows
        it and resends the POST body. A per-link error (the HTTP call itself succeeded)
        is reported on that ``LinkInspection`` and does not raise.
        """
        if not links:
            raise AllDebridError("LINK_IS_MISSING", "Aucun lien à vérifier.", retryable=False)
        payload = self._request("POST", "/v4/link/infos", data={"link[]": links})
        infos = (payload.get("data") or {}).get("infos") or []
        results: list[LinkInspection] = []
        for item in infos:
            if not isinstance(item, dict):
                continue
            results.append(_inspection(item))
        return results

    def saved_links(self) -> list[KnownFile]:
        return self._known("/v4/user/links", "saved")

    def history(self) -> list[KnownFile]:
        return self._known("/v4/user/history", "history")

    def delete_saved_links(self, links: list[str]) -> None:
        """POST /v4/user/links/delete. ``links`` are the saved hoster URLs, not CDN URLs."""
        if not links:
            return
        self._request("POST", "/v4/user/links/delete", data={"links[]": links})

    def delete_history(self, links: list[str] | None = None) -> None:
        """POST /v4/user/history/delete.

        With ``links``, those hoster URLs are sent as ``links[]``. Without them the
        official call wipes the whole recent history. Callers must not do that
        unless the person confirmed « tout purger ».
        """
        data = {"links[]": links} if links else None
        self._request("POST", "/v4/user/history/delete", data=data)

    def list_magnet_ids(self) -> list[str]:
        payload = self._request("GET", "/v4/magnet/status")
        rows = (payload.get("data") or {}).get("magnets") or []
        found: list[str] = []
        for row in rows:
            if isinstance(row, dict) and row.get("id") is not None:
                found.append(str(row["id"]))
        return found

    def delete_magnet(self, magnet_id: str) -> None:
        if not magnet_id:
            return
        self._request("POST", "/v4/magnet/delete", data={"id": str(magnet_id)})

    def upload_magnet(self, magnet: str) -> MagnetSnapshot:
        """POST /v4/magnet/upload. Call this only for a magnet the cache check already accepted."""
        payload = self._request("POST", "/v4/magnet/upload", data={"magnets[]": [magnet]})
        row = _first_magnet((payload.get("data") or {}).get("magnets"))
        if row is None:
            raise AllDebridError("MAGNET_INVALID", "AllDebrid n'a pas accepté l'aimant.", retryable=False)
        return _magnet_snapshot(row)

    def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
        payload = self._request("GET", f"/v4/magnet/status?id={magnet_id}")
        row = _first_magnet((payload.get("data") or {}).get("magnets"))
        if row is None:
            raise AllDebridError("MAGNET_MISSING", "Aimant introuvable chez AllDebrid.", retryable=True)
        return _magnet_snapshot(row)

    def magnet_cache(self, magnets: list[str]) -> list[MagnetCacheHit]:
        """Cache check only. Call this before any hoster link is unlocked.

        ``POST /v4/magnet/instant`` does not add the magnet. If AllDebrid drops the
        route, the caller keeps going with hoster links and does not upload the torrent.
        """
        if not magnets:
            return []
        payload = self._request("POST", "/v4/magnet/instant", data={"magnets[]": magnets})
        rows = (payload.get("data") or {}).get("magnets") or []
        hits: list[MagnetCacheHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cached = row.get("instant")
            if cached is None:
                cached = row.get("ready")
            name = row.get("name") or row.get("filename")
            hits.append(
                MagnetCacheHit(
                    magnet=str(row.get("magnet") or ""),
                    cached=bool(cached),
                    name=name if isinstance(name, str) and name else None,
                )
            )
        return hits

    def unlock(self, link: str, *, poll_timeout: float = 120.0, poll_interval: float = 5.0) -> DirectLink:
        payload = self._request("POST", "/v4/link/unlock", data={"link": link})
        data = payload.get("data") or {}
        direct = self._direct_from_payload(data)
        if direct is not None:
            return direct
        delayed_id = data.get("delayed")
        if not delayed_id:
            raise AllDebridError("LINK_EMPTY", "AllDebrid n'a pas renvoyé de lien direct.", retryable=True)
        return self._poll_delayed(str(delayed_id), poll_timeout=poll_timeout, poll_interval=poll_interval)

    def get_hosts(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if not force and self._hosts is not None and now - self._hosts_at < self.hosts_ttl:
            return self._hosts
        payload = self._request("GET", "/v4.1/user/hosts")
        raw = (payload.get("data") or {}).get("hosts") or {}
        hosts = _index_user_hosts(raw)
        self._hosts = hosts
        self._hosts_at = now
        return hosts

    def _poll_delayed(self, delayed_id: str, *, poll_timeout: float, poll_interval: float) -> DirectLink:
        deadline = time.monotonic() + poll_timeout
        while True:
            payload = self._request("POST", "/v4/link/delayed", data={"id": delayed_id})
            direct = self._direct_from_payload(payload.get("data") or {})
            if direct is not None:
                return direct
            if time.monotonic() >= deadline:
                raise AllDebridError(
                    "LINK_DELAYED_TIMEOUT",
                    "Le lien AllDebrid est encore en préparation.",
                    retryable=True,
                )
            self._sleeper(poll_interval)

    def _direct_from_payload(self, data: dict[str, Any]) -> DirectLink | None:
        url = data.get("link")
        if not url or not isinstance(url, str):
            return None
        size = data.get("filesize")
        # The CDN URL is a capability secret: never log it.
        logger.info(
            "AllDebrid unlock ok host=%s filename=%s size=%s",
            data.get("host"),
            data.get("filename"),
            size,
        )
        return DirectLink(
            url=url,
            filename=data.get("filename"),
            size=int(size) if isinstance(size, int) else None,
            host=data.get("host"),
        )

    def _known(self, path: str, source: str) -> list[KnownFile]:
        payload = self._request("GET", path)
        rows = (payload.get("data") or {}).get("links") or []
        found: list[KnownFile] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            link = row.get("link")
            if not isinstance(link, str) or not link:
                continue
            filename = row.get("filename") if isinstance(row.get("filename"), str) else ""
            size = row.get("size")
            host = row.get("host") if isinstance(row.get("host"), str) else None
            found.append(
                KnownFile(
                    link=link,
                    filename=filename,
                    size=int(size) if isinstance(size, int) else None,
                    host=host,
                    source=source,
                )
            )
        return found

    def _request_public(
        self,
        method: str,
        path: str,
        data: dict[str, str | list[str]] | None = None,
    ) -> dict[str, Any]:
        """PIN auth endpoints do not use the Bearer token."""
        from mediatracker.core.metrics.worker_counters import SOURCE_ALLDEBRID, record_source_request

        _pace(self._sleeper)
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": self.agent,
        }
        try:
            response = self._send_following_redirects(method, url, headers, data)
        except httpx.TransportError as exc:
            record_source_request(SOURCE_ALLDEBRID, error=True)
            raise AllDebridError("TRANSPORT", "AllDebrid injoignable.", retryable=True) from exc
        record_source_request(SOURCE_ALLDEBRID, error=response.status_code >= 400)
        return self._parse(response)

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, str | list[str]] | None = None,
    ) -> dict[str, Any]:
        from mediatracker.core.metrics.worker_counters import SOURCE_ALLDEBRID, record_source_request

        _pace(self._sleeper)
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": self.agent,
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._send_following_redirects(method, url, headers, data)
            except AllDebridError as exc:
                if exc.code in {"REDIRECT_BLOCKED", "REDIRECT_MISSING"}:
                    raise
                last_error = exc
                if not exc.retryable or attempt == self.max_retries - 1:
                    raise
            except httpx.TransportError as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    record_source_request(SOURCE_ALLDEBRID, error=True)
                    raise AllDebridError("TRANSPORT", "AllDebrid injoignable.", retryable=True) from exc
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = AllDebridError(
                        "TRANSPORT",
                        f"HTTP {response.status_code}",
                        retryable=True,
                    )
                    if attempt == self.max_retries - 1:
                        record_source_request(SOURCE_ALLDEBRID, error=True)
                        return self._parse(response)
                else:
                    record_source_request(SOURCE_ALLDEBRID, error=response.status_code >= 400)
                    return self._parse(response)
            self._sleeper(self.retry_delay * (2**attempt))
        record_source_request(SOURCE_ALLDEBRID, error=True)
        raise AllDebridError("TRANSPORT", str(last_error or "échec"), retryable=True)

    def _send_following_redirects(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: dict[str, str | list[str]] | None,
    ) -> httpx.Response:
        current_method = method
        current_url = self._with_agent(url)
        current_data = data
        for _hop in range(self.max_redirects + 1):
            response = self._client.request(
                current_method,
                current_url,
                headers=headers,
                data=current_data,
                follow_redirects=False,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("location")
            if not location:
                raise AllDebridError(
                    "REDIRECT_MISSING",
                    "Redirection AllDebrid sans en-tête Location.",
                    retryable=True,
                )
            next_url = str(httpx.URL(current_url).join(location))
            self._assert_allowed_redirect(next_url)
            # 301 from api.alldebrid.com points at apislow. Keep POST and the body:
            # converting to GET would drop the link parameter. 303 is See Other.
            if response.status_code == 303:
                current_method = "GET"
                current_data = None
            current_url = self._with_agent(next_url)
        raise AllDebridError("TOO_MANY_REDIRECTS", "Trop de redirections AllDebrid.", retryable=True)

    def _with_agent(self, url: str) -> str:
        parsed = httpx.URL(url)
        if "agent" in parsed.params:
            return str(parsed)
        return str(parsed.copy_add_param("agent", self.agent))

    def _assert_allowed_redirect(self, url: str) -> None:
        parsed = httpx.URL(url)
        host = (parsed.host or "").lower()
        if parsed.scheme != "https" or not _is_alldebrid_host(host):
            raise AllDebridError(
                "REDIRECT_BLOCKED",
                "Redirection AllDebrid refusée.",
                retryable=False,
            )

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AllDebridError(
                "BAD_RESPONSE",
                f"Réponse AllDebrid illisible (HTTP {response.status_code}).",
                retryable=response.status_code >= 500,
            ) from exc
        if not isinstance(payload, dict):
            raise AllDebridError("BAD_RESPONSE", "Réponse AllDebrid inattendue.", retryable=True)
        if response.status_code >= 400 or payload.get("status") == "error":
            code, message = _error_parts(payload)
            raise AllDebridError(code, message, retryable=code in RETRYABLE_CODES)
        return payload


def _redirector_links(payload: dict[str, Any]) -> list[str]:
    raw = (payload.get("data") or {}).get("links") or []
    found: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            found.append(item)
        elif isinstance(item, dict):
            link = item.get("link") or item.get("url")
            if isinstance(link, str) and link:
                found.append(link)
    return found


_SECRET = re.compile(r"(?i)\b(?:bearer|api[_-]?key|token|authorization)\b\s*[:=]?\s*\S+")
_HTTP_URL = re.compile(r"https?://\S+")


def _public_host(url: str) -> str:
    try:
        return httpx.URL(url).host or "inconnu"
    except Exception:
        return "inconnu"


def _scrub_message(message: str) -> str:
    """AllDebrid's own sentence, without URLs or credentials."""
    text = _HTTP_URL.sub("[lien]", message or "")
    text = _SECRET.sub("[redacted]", text)
    return text[:240]


def _is_alldebrid_host(host: str) -> bool:
    return host == "alldebrid.com" or host.endswith(".alldebrid.com")


def _inspection(item: dict[str, Any]) -> LinkInspection:
    error = item.get("error")
    link = item.get("link") if isinstance(item.get("link"), str) else ""
    if isinstance(error, dict):
        code = str(error.get("code") or "LINK_DOWN")
        message = str(error.get("message") or code)
        return LinkInspection(link=link, online=False, filename=None, size=None, code=code, message=message)
    filename = item.get("filename") if isinstance(item.get("filename"), str) else None
    size = item.get("size")
    return LinkInspection(
        link=link,
        online=True,
        filename=filename or None,
        size=int(size) if isinstance(size, int) else None,
        code=None,
        message=None,
    )


def _error_parts(payload: dict[str, Any]) -> tuple[str, str]:
    error = payload.get("error")
    if isinstance(error, str):
        return error, error
    if isinstance(error, dict):
        code = str(error.get("code") or "UNKNOWN")
        message = str(error.get("message") or code)
        return code, message
    return "UNKNOWN", "Erreur AllDebrid."


def _host_is_down(info: dict[str, Any]) -> bool:
    status = info.get("status")
    if status is False or status == 0:
        return True
    if isinstance(status, str) and status.lower() in {"down", "offline", "unavailable"}:
        return True
    return False


def _host_quota_exhausted(info: dict[str, Any]) -> bool:
    quota = info.get("quota")
    if quota == 0:
        return True
    limit = info.get("limitSimuDl")
    if limit == 0:
        return True
    return False


def _index_user_hosts(raw: Any) -> dict[str, dict[str, Any]]:
    hosts: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return hosts
    for key, info in raw.items():
        if not isinstance(info, dict):
            continue
        hosts[str(key).lower()] = info
        name = info.get("name")
        if isinstance(name, str) and name:
            hosts[name.lower()] = info
        for domain in info.get("domains") or []:
            if isinstance(domain, str) and domain:
                hosts[domain.lower()] = info
    return hosts


def _host_info_for_hoster(hosts: dict[str, dict[str, Any]], hoster: str) -> dict[str, Any] | None:
    domains = HOSTER_DOMAINS.get(hoster, (f"{hoster}.com", f"{hoster}.net"))
    for domain in domains:
        info = hosts.get(domain.lower())
        if info is not None:
            return info
    return hosts.get(hoster.lower())


def hoster_skip_reason_from_hosts(hoster: str, hosts: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    info = _host_info_for_hoster(hosts, hoster)
    if info is None:
        return None
    if _host_quota_exhausted(info):
        return (
            "HOST_QUOTA",
            f"Quota ou slot AllDebrid épuisé pour l'hébergeur « {hoster} ».",
        )
    if _host_is_down(info):
        return (
            "LINK_HOST_UNAVAILABLE",
            f"Hébergeur « {hoster} » indisponible chez AllDebrid.",
        )
    return None


def reorder_hoster_preference_for_cycle(preference: Sequence[str], client: AllDebridClient) -> list[str]:
    """Move AllDebrid-down or zero-quota hosters to the end for one unlock attempt cycle."""
    from mediatracker.core.selection import ensure_1fichier_last

    try:
        hosts = client.get_hosts()
    except AllDebridError:
        return list(preference)
    preferred: list[str] = []
    deferred: list[str] = []
    for name in preference:
        reason = hoster_skip_reason_from_hosts(name, hosts)
        if reason:
            deferred.append(name)
            logger.info("Hébergeur ignoré pour ce cycle : %s (%s)", name, reason[1])
        else:
            preferred.append(name)
    if not deferred:
        return list(preference)
    return ensure_1fichier_last(preferred + deferred)


class AllDebridProvider:
    kind = "debrid"
    type = "alldebrid"
    capabilities = frozenset({"redirector", "unlock"})
    client_factory: Callable[..., AllDebridClient] = AllDebridClient

    def __init__(self, config: AllDebridConfig) -> None:
        self.config = config
        self.client = self.client_factory(config.api_key)
        self._skip_reason: tuple[str, str] | None = None

    def close(self) -> None:
        self.client.close()

    @classmethod
    # Quoted: the class attribute ``type`` would shadow the builtin in this body.
    def config_model(cls) -> "type[BaseModel]":
        return AllDebridConfig

    def health(self) -> Health:
        checked_at = datetime.now(timezone.utc)
        if not self.config.api_key.strip():
            return Health(
                status="down",
                code="NOT_CONFIGURED",
                message="Clé API AllDebrid manquante.",
                checked_at=checked_at,
            )
        try:
            user = self.client.get_user()
        except AllDebridError as exc:
            if exc.code == "AUTH_BLOCKED":
                return Health(
                    status="down",
                    code="AUTH_BLOCKED",
                    message=AUTH_BLOCKED_MESSAGE,
                    checked_at=checked_at,
                )
            return Health(status="down", code=exc.code, message=exc.message, checked_at=checked_at)
        username = user.get("username") or "compte"
        if user.get("isPremium") is False:
            return Health(
                status="degraded",
                code="NOT_PREMIUM",
                message=f"Le compte AllDebrid « {username} » n'a pas d'abonnement premium actif.",
                checked_at=checked_at,
            )
        return Health(
            status="ok",
            code=None,
            message=f"Connecté à AllDebrid ({username}).",
            checked_at=checked_at,
        )

    def pop_skip_reason(self) -> tuple[str, str] | None:
        reason = self._skip_reason
        self._skip_reason = None
        return reason

    def preference_for_cycle(self, preference: list[str]) -> list[str]:
        return reorder_hoster_preference_for_cycle(preference, self.client)

    def supports(self, link: LinkCandidate) -> bool:
        self._skip_reason = None
        if link.kind == "ddl_protected":
            return "redirector" in self.capabilities
        if link.kind != "ddl_hoster":
            return False
        try:
            hosts = self.client.get_hosts()
        except AllDebridError:
            return True
        if not link.hoster:
            return True
        reason = hoster_skip_reason_from_hosts(link.hoster, hosts)
        if reason is not None:
            self._skip_reason = reason
            return False
        if _host_info_for_hoster(hosts, link.hoster) is not None:
            return True
        logger.info("Hébergeur %s absent des hôtes AllDebrid, la requête partira quand même.", link.hoster)
        return True

    def resolve(self, url: str) -> list[str]:
        return self.client.redirector(url)

    def unlock(self, url: str) -> DirectLink:
        return self.client.unlock(url)

    def inspect_link(self, url: str) -> LinkInspection:
        infos = self.client.link_infos([url])
        for info in infos:
            if info.link == url or not info.link:
                return info
        if infos:
            return infos[0]
        return LinkInspection(
            link=url,
            online=False,
            filename=None,
            size=None,
            code="LINK_IS_MISSING",
            message="AllDebrid n'a pas renvoyé d'information.",
        )

    def known_files(self) -> list[KnownFile]:
        return [*self.client.saved_links(), *self.client.history()]

    def saved_links(self):
        return self.client.saved_links()

    def delete_saved_links(self, links: list[str]) -> None:
        self.client.delete_saved_links(links)

    def delete_history(self, links: list[str] | None = None) -> None:
        self.client.delete_history(links)

    def list_magnet_ids(self) -> list[str]:
        return self.client.list_magnet_ids()

    def delete_magnet(self, magnet_id: str) -> None:
        self.client.delete_magnet(magnet_id)

    def magnet_cache(self, magnets: list[str]) -> list[MagnetCacheHit]:
        """Extension point for a future magnet source. Runs before hoster links."""
        return self.client.magnet_cache(magnets)

    def upload_magnet(self, magnet: str) -> MagnetSnapshot:
        return self.client.upload_magnet(magnet)

    def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
        return self.client.magnet_status(magnet_id)


def _first_magnet(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                return item
    return None


def _magnet_snapshot(row: dict[str, Any]) -> MagnetSnapshot:
    status_code = row.get("statusCode")
    status = str(row.get("status") or "")
    ready = bool(row.get("ready")) or status_code == 4 or status.lower() == "ready"
    return MagnetSnapshot(str(row.get("id") or ""), ready, _magnet_files(row.get("files")))


def _magnet_files(raw: object) -> list[MagnetFile]:
    found: list[MagnetFile] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            name = node.get("n") or node.get("name") or node.get("filename") or ""
            link = node.get("l") or node.get("link")
            size = node.get("s") if isinstance(node.get("s"), int) else node.get("size")
            if isinstance(name, str) and name and (isinstance(link, str) or "e" not in node):
                found.append(
                    MagnetFile(
                        index=len(found),
                        name=name,
                        size=size if isinstance(size, int) else None,
                        link=link if isinstance(link, str) else None,
                    )
                )
            for key in ("e", "files", "children"):
                if key in node:
                    walk(node[key])
            return
        if isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return found
