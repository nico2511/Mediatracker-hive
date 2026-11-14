"""Authentication dependencies. Cookie sessions are admin; bearer tokens carry scopes."""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mediatracker.api.errors import Problem
from mediatracker.db.models import ApiToken, User
from mediatracker.db.session import is_sqlite_locked
from mediatracker.security.sessions import SESSION_COOKIE
from mediatracker.security.tokens import expand_scopes, hash_token, split_token

logger = logging.getLogger(__name__)
# Home Assistant polls often. Rewriting last_used_at on every call collides with the worker.
_TOKEN_TOUCH = timedelta(minutes=5)


@dataclass
class Principal:
    user_id: int
    username: str
    via: Literal["session", "bearer"]
    scopes: set[str]


def get_db(request: Request):
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _user_or_401(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise Problem(401, "Non authentifié", "Session invalide.")
    return user


def authenticate_bearer(db: Session, token: str) -> Principal:
    prefix = split_token(token)
    if prefix is None:
        raise Problem(401, "Non authentifié", "Jeton invalide.")
    row = db.scalar(select(ApiToken).where(ApiToken.prefix == prefix))
    if row is None or row.revoked_at is not None:
        raise Problem(401, "Non authentifié", "Jeton invalide.")
    digest = hash_token(token)
    if len(digest) != len(row.token_hash) or not secrets.compare_digest(digest, row.token_hash):
        raise Problem(401, "Non authentifié", "Jeton invalide.")
    expires = _as_utc(row.expires_at)
    now = datetime.now(timezone.utc)
    if expires is not None and expires <= now:
        raise Problem(401, "Non authentifié", "Jeton expiré.")
    _touch_token(db, row, now)
    user = _user_or_401(db, row.user_id)
    scopes = expand_scopes({str(scope) for scope in (row.scopes or [])})
    return Principal(user_id=user.id, username=user.username, via="bearer", scopes=scopes)


def _touch_token(db: Session, row: ApiToken, now: datetime) -> None:
    """Record use at most every five minutes. A locked write must not fail the request."""
    seen = _as_utc(row.last_used_at)
    if seen is not None and now - seen < _TOKEN_TOUCH:
        return
    row.last_used_at = now
    try:
        db.commit()
    except OperationalError as exc:
        db.rollback()
        if not is_sqlite_locked(exc):
            raise
        logger.info("Horodatage du jeton ignoré : base occupée.")


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
        request.state.auth_via = "bearer"
        return authenticate_bearer(db, token)
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        parsed = request.app.state.sessions.loads(raw)
        if parsed is not None:
            user_id, token_epoch = parsed
            user = _user_or_401(db, user_id)
            if token_epoch != user.session_epoch:
                raise Problem(401, "Non authentifié", "Session invalide.")
            request.state.session_user_id = user.id
            request.state.session_epoch = user.session_epoch
            request.state.refresh_session = True
            request.state.auth_via = "session"
            return Principal(
                user_id=user.id,
                username=user.username,
                via="session",
                scopes={"admin", "write", "read"},
            )
    raise Problem(401, "Non authentifié", "Authentification requise.")


def require_scope(scope: str):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if scope not in principal.scopes:
            raise Problem(403, "Portée insuffisante", "Ce jeton n'a pas la portée requise.")
        return principal

    return dependency
