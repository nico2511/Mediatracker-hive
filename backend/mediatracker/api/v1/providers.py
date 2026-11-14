"""Provider instances. Config secrets are encrypted and never returned."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem, public_validation_errors
from mediatracker.db.models import Provider
from mediatracker.events import record_event
from mediatracker.integrations.alldebrid_key import effective_alldebrid_key
from mediatracker.providers.base import Health
from mediatracker.providers.registry import UnknownProvider, secret_fields
from mediatracker.security.crypto import decrypt_secret, encrypt_secret

router = APIRouter(tags=["providers"])


class ProviderCreate(BaseModel):
    kind: str = Field(pattern="^(source|debrid|downloader)$")
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    priority: int = Field(default=0, ge=-1000, le=1000)
    config: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)
    config: dict[str, Any] | None = None


class ProviderOut(BaseModel):
    id: int
    kind: str
    type: str
    name: str
    enabled: bool
    priority: int
    builtin: bool
    config: dict[str, Any]
    secrets_set: list[str]
    capabilities: list[str]
    config_schema: dict[str, Any]
    health_status: str | None
    health_code: str | None
    health_message: str | None
    health_checked_at: datetime | None


class ProviderPage(BaseModel):
    items: list[ProviderOut]
    next_cursor: str | None = None


class ProviderTypeOut(BaseModel):
    kind: str
    type: str
    capabilities: list[str]
    config_schema: dict[str, Any]
    download_rank: int | None = None


class ProviderTypePage(BaseModel):
    items: list[ProviderTypeOut]


def _cursor(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", "Curseur invalide.") from exc
    if parsed < 0:
        raise Problem(422, "Requête invalide", "Curseur invalide.")
    return parsed


def _model(registry, kind: str, type_: str):
    try:
        return registry.get(kind, type_)
    except UnknownProvider as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc


def _decrypt_config(model, stored: dict | None, secret_key: str) -> dict[str, Any]:
    names = secret_fields(model.config_model())
    plain: dict[str, Any] = {}
    for key, value in (stored or {}).items():
        if key in names and isinstance(value, str) and value:
            plain[key] = decrypt_secret(value, secret_key)
        else:
            plain[key] = value
    return plain


def _encrypt_config(model, plain: dict[str, Any], secret_key: str) -> dict[str, Any]:
    names = secret_fields(model.config_model())
    stored: dict[str, Any] = {}
    for key, value in plain.items():
        if key in names and isinstance(value, str) and value:
            stored[key] = encrypt_secret(value, secret_key)
        else:
            stored[key] = value
    return stored


def _mask(model, plain: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    names = secret_fields(model.config_model())
    masked: dict[str, Any] = {}
    set_fields: list[str] = []
    for key, value in plain.items():
        if key in names:
            if value:
                set_fields.append(key)
            masked[key] = None
        else:
            masked[key] = value
    return masked, sorted(set_fields)


def _validate(model, plain: dict[str, Any]):
    try:
        return model.config_model().model_validate(plain)
    except ValidationError as exc:
        raise Problem(
            422,
            "Requête invalide",
            "Configuration du fournisseur invalide.",
            extra={"errors": public_validation_errors(exc.errors())},
        ) from exc


def _to_out(row: Provider, registry, secret_key: str) -> ProviderOut:
    model = _model(registry, row.kind, row.type)
    plain = _decrypt_config(model, row.config, secret_key)
    masked, secrets_set = _mask(model, plain)
    return ProviderOut(
        id=row.id,
        kind=row.kind,
        type=row.type,
        name=row.name,
        enabled=row.enabled,
        priority=row.priority,
        builtin=row.builtin,
        config=masked,
        secrets_set=secrets_set,
        capabilities=sorted(model.capabilities),
        config_schema=model.config_model().model_json_schema(),
        health_status=row.health_status,
        health_code=row.health_code,
        health_message=row.health_message,
        health_checked_at=row.health_checked_at,
    )


def _merge_config(current: dict[str, Any], updates: dict[str, Any], names: set[str]) -> dict[str, Any]:
    merged = dict(current)
    for key, value in updates.items():
        if key in names and value is None:
            continue
        merged[key] = "" if key in names and value == "" else value
    return merged


def _get_row(db: Session, provider_id: int) -> Provider:
    row = db.get(Provider, provider_id)
    if row is None:
        raise Problem(404, "Introuvable", "Fournisseur introuvable.")
    return row


@router.get("/providers/types", response_model=ProviderTypePage)
def list_provider_types(
    request: Request,
    kind: str | None = None,
    _principal=Depends(require_scope("read")),
) -> ProviderTypePage:
    del _principal
    items = []
    for model in request.app.state.registry.known(kind):
        caps = set(model.capabilities)
        rank = getattr(model, "download_rank", None)
        if "hive" in caps:
            rank = getattr(model, "download_rank", None)
        elif "links" not in caps and "episode" not in caps:
            rank = None
        items.append(
            ProviderTypeOut(
                kind=model.kind,
                type=model.type,
                capabilities=sorted(model.capabilities),
                config_schema=model.config_model().model_json_schema(),
                download_rank=rank,
            )
        )
    return ProviderTypePage(items=items)


@router.get("/providers", response_model=ProviderPage)
def list_providers(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> ProviderPage:
    del _principal
    stmt = select(Provider)
    if kind is not None:
        stmt = stmt.where(Provider.kind == kind)
    if cursor is not None:
        stmt = stmt.where(Provider.id > _cursor(cursor))
    rows = list(db.scalars(stmt.order_by(Provider.id).limit(limit + 1)).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    secret_key = request.app.state.settings.secret_key
    return ProviderPage(
        items=[_to_out(row, request.app.state.registry, secret_key) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(
    body: ProviderCreate,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> ProviderOut:
    del _principal
    registry = request.app.state.registry
    model = _model(registry, body.kind, body.type)
    if body.type == "manual" or body.type == "aria2":
        raise Problem(409, "Conflit", "Ce fournisseur intégré existe déjà.")
    validated = _validate(model, body.config)
    row = Provider(
        kind=body.kind,
        type=body.type,
        name=body.name,
        enabled=body.enabled,
        priority=body.priority,
        config=_encrypt_config(model, validated.model_dump(), request.app.state.settings.secret_key),
        builtin=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row, registry, request.app.state.settings.secret_key)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> ProviderOut:
    del _principal
    row = _get_row(db, provider_id)
    registry = request.app.state.registry
    model = _model(registry, row.kind, row.type)
    if row.builtin and body.enabled is False:
        raise Problem(409, "Conflit", "Ce fournisseur intégré reste actif.")
    if body.name is not None:
        row.name = body.name
    if body.enabled is not None:
        row.enabled = body.enabled
    if body.priority is not None:
        row.priority = body.priority
    if body.config is not None:
        current = _decrypt_config(model, row.config, request.app.state.settings.secret_key)
        merged = _merge_config(current, body.config, secret_fields(model.config_model()))
        validated = _validate(model, merged)
        row.config = _encrypt_config(model, validated.model_dump(), request.app.state.settings.secret_key)
    db.commit()
    db.refresh(row)
    return _to_out(row, registry, request.app.state.settings.secret_key)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> Response:
    del _principal
    row = _get_row(db, provider_id)
    if row.builtin:
        raise Problem(409, "Conflit", "Ce fournisseur intégré ne peut pas être supprimé.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/providers/{provider_id}/health", response_model=Health)
def provider_health(
    provider_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> Health:
    """Sondage santé d'un fournisseur (admin / intégration, sans écran dédié dans l'UI)."""
    del _principal
    row = _get_row(db, provider_id)
    settings = request.app.state.settings
    alldebrid_key = (
        effective_alldebrid_key(settings, db) if row.kind == "debrid" and row.type == "alldebrid" else ""
    )
    if not row.enabled and not alldebrid_key:
        health = Health(
            status="down",
            code="DISABLED",
            message="Fournisseur désactivé.",
            checked_at=datetime.now(timezone.utc),
        )
    else:
        registry = request.app.state.registry
        model = _model(registry, row.kind, row.type)
        plain = _decrypt_config(model, row.config, settings.secret_key)
        if row.kind == "debrid" and row.type == "alldebrid":
            plain["api_key"] = alldebrid_key
        try:
            if row.kind == "source" and row.type == "wawacity":
                from mediatracker.providers.sources.loading import wawacity_config
                from mediatracker.providers.sources.wawacity import WawacitySource

                instance = WawacitySource(wawacity_config(db, request.app.state.settings))
                try:
                    health = instance.health()
                finally:
                    instance.close()
            else:
                instance = registry.instantiate(row.kind, row.type, plain)
                health = instance.health()
        except Exception as exc:  # a provider failure is a health result, not a 500
            health = Health(
                status="down",
                code="ERROR",
                message=str(exc)[:300] or "Contrôle de santé impossible.",
                checked_at=datetime.now(timezone.utc),
            )
    changed = row.health_status != health.status or row.health_code != health.code
    row.health_status = health.status
    row.health_code = health.code
    row.health_message = health.message
    row.health_checked_at = health.checked_at
    if changed:
        record_event(
            db,
            "provider.health_changed",
            level="info" if health.status == "ok" else "warning",
            payload={
                "provider_id": row.id,
                "type": row.type,
                "status": health.status,
                "code": health.code,
                "message": health.message,
            },
        )
    db.commit()
    return health
