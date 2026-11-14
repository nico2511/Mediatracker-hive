from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import Principal, get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.db.models import ApiToken
from mediatracker.security.tokens import TOKEN_SCOPES, generate_token

router = APIRouter(prefix="/tokens", tags=["tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime | None = None


class TokenOut(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class TokenCreated(TokenOut):
    token: str


class TokenPage(BaseModel):
    items: list[TokenOut]
    next_cursor: str | None = None


def _out(row: ApiToken) -> TokenOut:
    return TokenOut(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=list(row.scopes or []),
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


@router.get("", response_model=TokenPage)
def list_tokens(
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("admin")),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> TokenPage:
    stmt = select(ApiToken).where(ApiToken.user_id == principal.user_id, ApiToken.revoked_at.is_(None))
    if cursor is not None:
        stmt = stmt.where(ApiToken.id > _cursor(cursor))
    rows = list(db.scalars(stmt.order_by(ApiToken.id).limit(limit + 1)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    return TokenPage(items=[_out(row) for row in rows], next_cursor=next_cursor)


@router.post("", response_model=TokenCreated, status_code=201)
def create_token(
    body: TokenCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("admin")),
) -> TokenCreated:
    unknown = [scope for scope in body.scopes if scope not in TOKEN_SCOPES]
    if unknown:
        raise Problem(422, "Requête invalide", "Portée inconnue.")
    if body.expires_at is not None:
        expires = body.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            raise Problem(422, "Requête invalide", "La date d'expiration est déjà passée.")
    full, prefix, digest = generate_token()
    while db.scalar(select(ApiToken.id).where(ApiToken.prefix == prefix)) is not None:
        full, prefix, digest = generate_token()
    row = ApiToken(
        user_id=principal.user_id,
        name=body.name,
        prefix=prefix,
        token_hash=digest,
        scopes=list(dict.fromkeys(body.scopes)),
        expires_at=body.expires_at,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    base = _out(row)
    return TokenCreated(token=full, **base.model_dump())


@router.delete("/{token_id}", status_code=204)
def revoke_token(
    token_id: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_scope("admin")),
) -> Response:
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != principal.user_id or row.revoked_at is not None:
        raise Problem(404, "Introuvable", "Jeton introuvable.")
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return Response(status_code=204)


def _cursor(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", "Curseur invalide.") from exc
    if parsed < 0:
        raise Problem(422, "Requête invalide", "Curseur invalide.")
    return parsed
