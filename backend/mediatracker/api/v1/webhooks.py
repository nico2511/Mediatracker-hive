"""Webhook registration. Delivery runs later; the subscription is stored now.

``library.imported`` is the event sent when a file lands on the NAS.

- ``generic`` : POST signé HMAC, secret obligatoire.
- ``ntfy`` : message titre/tags/priorité, jeton Bearer optionnel.
- ``homeassistant`` : POST JSON vers ``http://<hôte>:8123/api/webhook/<id>``.
  L'identifiant dans l'URL est le secret ; pas d'en-tête HMAC.
"""

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.db.models import Webhook
from mediatracker.events.catalog import EVENT_TYPES
from mediatracker.events.deliver import send_test
from mediatracker.security.crypto import decrypt_secret, encrypt_secret
from mediatracker.security.ssrf import OutboundUrlRejected, settings_rejection_message, validate_outbound_url_static

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

WebhookType = Literal["generic", "ntfy", "homeassistant"]


class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2000)
    type: WebhookType = "generic"
    events: list[str] = Field(min_length=1)
    secret: str | None = None
    enabled: bool = True


class WebhookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = None
    events: list[str] | None = None
    secret: str | None = None
    enabled: bool | None = None


class WebhookOut(BaseModel):
    id: int
    name: str
    url: str
    type: str
    events: list[str]
    enabled: bool
    secret_set: bool
    failure_count: int


class WebhookTestOut(BaseModel):
    ok: bool
    status: int
    message: str


def _check_url(url: str) -> None:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise Problem(422, "Requête invalide", "L'URL du webhook doit commencer par http:// ou https://.")
    try:
        validate_outbound_url_static(url)
    except OutboundUrlRejected:
        raise Problem(422, "Requête invalide", settings_rejection_message()) from None


def _check_homeassistant_url(url: str) -> None:
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or len(segments) != 3
        or segments[0] != "api"
        or segments[1] != "webhook"
        or not segments[2]
    ):
        raise Problem(
            422,
            "Requête invalide",
            "L'URL Home Assistant doit être de la forme http://hôte:8123/api/webhook/<id>.",
        )
    try:
        validate_outbound_url_static(url)
    except OutboundUrlRejected:
        raise Problem(422, "Requête invalide", settings_rejection_message()) from None


def _check_events(events: list[str]) -> list[str]:
    if any(event not in EVENT_TYPES for event in events):
        raise Problem(422, "Requête invalide", "Événement inconnu.")
    unique = list(dict.fromkeys(events))
    if not unique:
        raise Problem(422, "Requête invalide", "Au moins un événement est requis.")
    return unique


def _check_secret(kind: str, secret: str | None, *, creating: bool) -> None:
    if kind == "homeassistant":
        if secret:
            raise Problem(
                422,
                "Requête invalide",
                "Un webhook Home Assistant n'a pas de secret : l'identifiant est dans l'URL.",
            )
        return
    if kind == "generic" and creating and not secret:
        raise Problem(422, "Requête invalide", "Un secret HMAC est requis pour un webhook générique.")
    if secret and len(secret) < 8:
        raise Problem(422, "Requête invalide", "Le secret doit contenir au moins 8 caractères.")


def _out(row: Webhook, secret_key: str) -> WebhookOut:
    plain = decrypt_secret(row.secret, secret_key) if row.secret else ""
    return WebhookOut(
        id=row.id,
        name=row.name,
        url=row.url,
        type=row.type,
        events=list(row.events or []),
        enabled=row.enabled,
        secret_set=bool(plain),
        failure_count=row.failure_count,
    )


def _get(db: Session, webhook_id: int) -> Webhook:
    row = db.get(Webhook, webhook_id)
    if row is None:
        raise Problem(404, "Introuvable", "Webhook introuvable.")
    return row


@router.get("", response_model=list[WebhookOut])
def list_webhooks(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> list[WebhookOut]:
    del _principal
    secret_key = request.app.state.settings.secret_key
    rows = db.scalars(select(Webhook).order_by(Webhook.id)).all()
    return [_out(row, secret_key) for row in rows]


@router.post("", response_model=WebhookOut, status_code=201)
def create_webhook(
    body: WebhookCreate,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> WebhookOut:
    del _principal
    if body.type == "homeassistant":
        _check_homeassistant_url(body.url)
    else:
        _check_url(body.url)
    events = _check_events(body.events)
    _check_secret(body.type, body.secret, creating=True)
    secret_key = request.app.state.settings.secret_key
    stored = encrypt_secret(body.secret, secret_key) if body.secret else ""
    row = Webhook(
        name=body.name,
        url=body.url,
        type=body.type,
        events=events,
        secret=stored,
        enabled=body.enabled,
        failure_count=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row, secret_key)


@router.patch("/{webhook_id}", response_model=WebhookOut)
def update_webhook(
    webhook_id: int,
    body: WebhookPatch,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> WebhookOut:
    del _principal
    row = _get(db, webhook_id)
    secret_key = request.app.state.settings.secret_key
    data = body.model_dump(exclude_unset=True)
    if "url" in data and data["url"] is not None:
        if row.type == "homeassistant":
            _check_homeassistant_url(data["url"])
        else:
            _check_url(data["url"])
        row.url = data["url"]
    if "name" in data and data["name"] is not None:
        row.name = data["name"]
    if "events" in data and data["events"] is not None:
        row.events = _check_events(data["events"])
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = data["enabled"]
    if "secret" in data:
        secret = data["secret"]
        _check_secret(row.type, secret, creating=False)
        if secret == "":
            if row.type == "generic":
                raise Problem(422, "Requête invalide", "Un webhook générique ne peut pas perdre son secret.")
            row.secret = ""
        elif secret is not None:
            row.secret = encrypt_secret(secret, secret_key)
    db.commit()
    db.refresh(row)
    return _out(row, secret_key)


@router.post("/{webhook_id}/test", response_model=WebhookTestOut)
def test_webhook(
    webhook_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> WebhookTestOut:
    del _principal
    row = _get(db, webhook_id)
    ok, status, message = send_test(db, request.app.state.settings, row, request.app.state.settings.secret_key)
    return WebhookTestOut(ok=ok, status=status, message=message)


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> Response:
    del _principal
    row = _get(db, webhook_id)
    db.delete(row)
    db.commit()
    return Response(status_code=204)
