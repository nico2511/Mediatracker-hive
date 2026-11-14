"""URL publique de l'application (schéma + hôte, sans chemin)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

if TYPE_CHECKING:
    from mediatracker.config import Settings

_PUBLIC_BASE_KEY = "public_base_url"


def normalize_public_url(text: str) -> str:
    """Valide et normalise une URL publique (origine seule, slash final retiré)."""
    raw = text.strip()
    if not raw:
        raise ValueError("PUBLIC_URL ne peut pas être vide.")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("PUBLIC_URL doit commencer par http:// ou https://.")
    if parts.username or parts.password or not parts.hostname:
        raise ValueError("PUBLIC_URL ne doit pas contenir d'identifiant.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("PUBLIC_URL ne doit pas contenir de chemin, de requête ni de fragment.")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{parts.hostname}{port}".rstrip("/")


def coerce_public_url(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return normalize_public_url(text)
    raise ValueError("PUBLIC_URL doit être une chaîne.")


def public_url_scheme(url: str | None) -> str | None:
    if not url:
        return None
    return urlsplit(url).scheme.lower()


def request_host_matches_public_url(
    public_url: str,
    hostname: str | None,
    port: int | None = None,
    request_scheme: str = "http",
) -> bool:
    """True si le nom d'hôte de la requête est celui de PUBLIC_URL (``port`` ignoré)."""
    del port, request_scheme
    if not hostname:
        return False
    parts = urlsplit(public_url)
    if not parts.hostname:
        return False
    return parts.hostname.lower() == hostname.lower()


def _stored_public_base_url(db: Session) -> str:
    row = db.get(Setting, _PUBLIC_BASE_KEY)
    if row is None or not row.value:
        return ""
    return row.value.strip()


def effective_public_base_url(db: Session, settings: Settings) -> str | None:
    """URL publique effective : réglage en base, sinon variable d'environnement."""
    stored = _stored_public_base_url(db)
    if stored:
        return stored.rstrip("/")
    env = settings.public_url
    return env.rstrip("/") if env else None
