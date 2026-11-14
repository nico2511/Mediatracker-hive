"""Trigger a Watchtower image update over its HTTP API (no docker.sock in MediaTracker)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.updates import _secret, update_image
from mediatracker.db.models import Setting
from mediatracker.events import record_event
from mediatracker.security.logging import redact

logger = logging.getLogger(__name__)
_USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")


@dataclass(frozen=True)
class UpdateNowResult:
    status: int
    detail: str
    code: str
    ok: bool = True


def watchtower_configured(db: Session, settings: Settings) -> bool:
    url, token = _credentials(db, settings)
    return bool(url and token)


def request_update_now(
    db: Session,
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> UpdateNowResult:
    url, token = _credentials(db, settings)
    if not url or not token:
        raise ValueError("watchtower not configured")

    target = f"{url.rstrip('/')}/v1/update"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"image": update_image()}
    timeout = httpx.Timeout(8.0, connect=3.0)
    url_label = _url_for_message(url)
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.post(target, params=params, headers=headers)
    except httpx.ConnectError as exc:
        _log_failure(exc)
        return UpdateNowResult(
            502,
            f"Watchtower est injoignable. Erreur de connexion à {url_label}. "
            "Vérifiez que le conteneur tourne et que l'URL est joignable depuis MediaTracker.",
            "unreachable",
        )
    except httpx.ConnectTimeout as exc:
        _log_failure(exc)
        return UpdateNowResult(
            502,
            f"Watchtower est injoignable. Délai de connexion à {url_label}. "
            "Vérifiez que le conteneur tourne et que l'URL est joignable depuis MediaTracker.",
            "unreachable",
        )
    except httpx.ReadTimeout:
        _record_requested(db)
        return UpdateNowResult(
            202,
            "Demande envoyée à Watchtower. L'appli va redémarrer d'ici une minute environ.",
            "sent",
        )
    except httpx.HTTPError as exc:
        _log_failure(exc)
        return UpdateNowResult(502, "Watchtower a répondu une erreur.", "watchtower_error")

    if response.status_code == 401:
        _log_http_failure(response.status_code)
        return UpdateNowResult(
            502,
            "Watchtower a refusé le jeton. Le jeton enregistré ne correspond pas à "
            "WATCHTOWER_HTTP_API_TOKEN. Corrigez-le dans Jetons d'API.",
            "token_refused",
        )
    if response.status_code == 200:
        _record_requested(db)
        return UpdateNowResult(
            200,
            "Rien à installer. Watchtower n'a pas trouvé d'image plus récente : MediaTracker est déjà à jour.",
            "already_up_to_date",
        )

    _log_http_failure(response.status_code)
    return UpdateNowResult(502, "Watchtower a répondu une erreur.", "watchtower_error")


def _credentials(db: Session, settings: Settings) -> tuple[str, str]:
    url = _plain_setting(db, settings, "watchtower_url")
    token = _secret(db, "watchtower_token", settings)
    return url.strip(), token.strip()


def _plain_setting(db: Session, settings: Settings, key: str) -> str:
    row = db.get(Setting, key)
    if row is not None and row.value:
        return row.value.strip()
    fallback = getattr(settings, key, None)
    return fallback.strip() if isinstance(fallback, str) else ""


def _record_requested(db: Session) -> None:
    record_event(
        db,
        "update.requested",
        level="info",
        payload={"title": "MediaTracker", "level": "info"},
        dedupe_key=None,
    )


def _url_for_message(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        netloc = host
    else:
        netloc = parts.netloc
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)).rstrip("/")


def _log_failure(exc: BaseException) -> None:
    logger.warning(
        "Mise à jour Watchtower impossible (%s) : %s",
        type(exc).__name__,
        _USERINFO.sub(r"\1[redacted]@", redact(str(exc))),
    )


def _log_http_failure(status: int) -> None:
    logger.warning("Mise à jour Watchtower impossible (HTTP %s).", status)
