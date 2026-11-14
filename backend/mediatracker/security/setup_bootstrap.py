"""Jeton d'amorçage pour la création du premier compte (mémoire uniquement)."""

import hashlib
import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import User

logger = logging.getLogger(__name__)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def token_matches(expected: str | None, provided: str) -> bool:
    """Comparaison en temps constant, y compris si les longueurs diffèrent."""
    normalized = (expected or "").strip()
    if not normalized:
        return False
    return secrets.compare_digest(_digest(normalized), _digest(provided))


def prepare_setup_bootstrap_token(db: Session, settings: Settings) -> str | None:
    """Retourne le jeton attendu tant qu'aucun compte n'existe, sinon ``None``."""
    count = db.scalar(select(func.count()).select_from(User)) or 0
    if count:
        return None
    env_token = (settings.setup_token or "").strip()
    if env_token:
        return env_token
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "Aucun compte administrateur : pour créer le premier compte via l'assistant, "
        "saisissez le jeton d'installation suivant (ou définissez SETUP_TOKEN pour le fixer) : %s",
        generated,
    )
    return generated
