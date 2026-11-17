"""Jellyfin settings probe and the webhook the plugin calls on the LAN."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.jellyfin_web import jellyfin_base_url, jellyfin_home_url
from mediatracker.core.public_url import effective_public_base_url
from mediatracker.db.models import Setting
from mediatracker.events import record_event
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, match_jellyfin_user
from mediatracker.integrations.jellyfin_sync import (
    LAST_ERROR,
    LAST_POLL_AT,
    _poll_minutes,
    apply_webhook,
    jellyfin_settings,
    save_webhook_secret,
    sync_watched_now,
    webhook_secret,
    webhook_token_preview,
)
from mediatracker.integrations.jellyfin_webhook_auth import (
    WEBHOOK_TOKEN_HEADER,
    note_webhook_query_token_usage,
    resolve_webhook_token,
    webhook_token_valid,
)
from mediatracker.integrations.paths import library_path_warnings, parse_path_map, suggest_path_map
from mediatracker.library.scanner import library_roots

router = APIRouter(prefix="/integrations/jellyfin", tags=["jellyfin"])


class JellyfinWebOut(BaseModel):
    url: str | None = None


class JellyfinStatus(BaseModel):
    configured: bool
    url: str | None
    user_id: str | None
    path_prefix: str | None
    path_map: str | None
    poll_interval_min: int
    webhook_url: str | None
    webhook_token_set: bool
    webhook_token_preview: str | None
    webhook_token_header: str = WEBHOOK_TOKEN_HEADER
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None


class JellyfinTestIn(BaseModel):
    url: str | None = None
    api_key: str | None = None
    user_id: str | None = None
    path_map: str | None = None


class JellyfinLibraryOut(BaseModel):
    name: str
    collection_type: str
    locations: list[str]


class JellyfinTestOut(BaseModel):
    ok: bool
    server_name: str | None = None
    version: str | None = None
    user_name: str | None = None
    user_id: str | None = None
    path_warnings: list[str] = []
    suggested_path_map: str = ""
    libraries: list[JellyfinLibraryOut] = []
    message: str


class WebhookOut(BaseModel):
    updated: int
    scheduled: bool = False


class WebhookTokenOut(BaseModel):
    webhook_token: str


_NO_STORE = {"Cache-Control": "no-store"}


class SyncOut(BaseModel):
    updated: int
    message: str


class JellyfinUserOut(BaseModel):
    id: str
    name: str


class JellyfinUsersIn(BaseModel):
    url: str | None = None
    api_key: str | None = None


class JellyfinUsersOut(BaseModel):
    users: list[JellyfinUserOut]


def build_client(url: str, api_key: str, user_id: str | None) -> JellyfinClient:
    return JellyfinClient(url, api_key, user_id)


@router.get("/web", response_model=JellyfinWebOut)
def jellyfin_web(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> JellyfinWebOut:
    """Home page of the configured Jellyfin web client. No API key."""
    del _principal
    return JellyfinWebOut(url=jellyfin_home_url(jellyfin_base_url(db)))


@router.get("", response_model=JellyfinStatus)
def jellyfin_status(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> JellyfinStatus:
    del _principal
    url, _key, user_id, prefix, path_map = jellyfin_settings(db, request.app.state.settings)
    secret = webhook_secret(db, request.app.state.settings)
    settings = request.app.state.settings
    base = effective_public_base_url(db, settings) or str(request.base_url).rstrip("/")
    webhook = f"{base}/api/v1/integrations/jellyfin/webhook" if secret else None
    interval = _poll_minutes(db, settings)
    return JellyfinStatus(
        configured=bool(url and _key),
        url=url or None,
        user_id=user_id or None,
        path_prefix=prefix or None,
        path_map=path_map or None,
        poll_interval_min=interval,
        webhook_url=webhook,
        webhook_token_set=bool(secret),
        webhook_token_preview=webhook_token_preview(secret),
        last_sync_at=_sync_time(db),
        last_sync_error=_sync_error(db),
    )


@router.post("/webhook-token/reveal", response_model=WebhookTokenOut)
def reveal_jellyfin_webhook_token(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> JSONResponse:
    del _principal
    secret = webhook_secret(db, request.app.state.settings)
    if not secret:
        raise Problem(422, "Jellyfin", "Jeton webhook absent.")
    record_event(db, "jellyfin.webhook_token_revealed", payload={})
    db.commit()
    return JSONResponse(
        content=WebhookTokenOut(webhook_token=secret).model_dump(),
        headers=_NO_STORE,
    )


@router.post("/webhook-token/regenerate", response_model=WebhookTokenOut)
def regenerate_jellyfin_webhook_token(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> JSONResponse:
    del _principal
    moment = datetime.now(timezone.utc)
    secret = secrets.token_urlsafe(32)
    save_webhook_secret(db, request.app.state.settings, secret, moment)
    record_event(db, "jellyfin.webhook_token_regenerated", payload={})
    db.commit()
    return JSONResponse(
        content=WebhookTokenOut(webhook_token=secret).model_dump(),
        headers=_NO_STORE,
    )


@router.post("/sync", response_model=SyncOut)
def sync_jellyfin_watched(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> SyncOut:
    """Fetch played state for the configured Jellyfin user and apply it now."""
    del _principal
    try:
        updated = sync_watched_now(db, request.app.state.settings)
    except JellyfinError as exc:
        raise Problem(422, "Jellyfin", exc.message) from exc
    return SyncOut(updated=updated, message=f"{updated} fiche(s) mise(s) à jour.")


@router.post("/users", response_model=JellyfinUsersOut)
def list_jellyfin_users(
    body: JellyfinUsersIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> JellyfinUsersOut:
    """Accounts on the Jellyfin server, so Réglages can offer a user instead of a free-text id."""
    del _principal
    stored_url, stored_key, _stored_user, _prefix, _stored_map = jellyfin_settings(db, request.app.state.settings)
    url = (body.url or stored_url).strip()
    api_key = body.api_key if body.api_key else stored_key
    if not url or not api_key:
        raise Problem(422, "Jellyfin", "Renseignez l'URL et la clé API, puis enregistrez.")
    client = build_client(url, api_key, None)
    try:
        rows = client.users()
    except JellyfinError as exc:
        raise Problem(502, "Jellyfin", exc.message) from exc
    finally:
        client.close()
    users = []
    for row in rows:
        user_id = row.get("Id")
        name = row.get("Name")
        if isinstance(user_id, str) and user_id and isinstance(name, str) and name:
            users.append(JellyfinUserOut(id=user_id, name=name))
    return JellyfinUsersOut(users=users)


@router.post("/test", response_model=JellyfinTestOut)
def test_jellyfin(
    body: JellyfinTestIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> JellyfinTestOut:
    del _principal
    stored_url, stored_key, stored_user, prefix, stored_map = jellyfin_settings(db, request.app.state.settings)
    url = (body.url or stored_url).strip()
    api_key = body.api_key if body.api_key else stored_key
    user_id = (body.user_id if body.user_id is not None else stored_user).strip()
    if not url or not api_key:
        raise Problem(422, "Jellyfin", "Renseignez l'URL et la clé API, puis enregistrez.")
    client = build_client(url, api_key, user_id or None)
    try:
        info = client.system_info()
        detected_id, user_name = _resolve_user(client, user_id)
        folders = _folders(client)
    except JellyfinError as exc:
        return JellyfinTestOut(ok=False, message=exc.message)
    finally:
        client.close()
    movies, series = library_roots(db, request.app.state.settings)
    raw_map = stored_map if body.path_map is None else body.path_map
    try:
        mappings = parse_path_map(raw_map)
    except ValueError as exc:
        raise Problem(422, "Jellyfin", str(exc)) from exc
    warnings = library_path_warnings(str(movies), str(series), folders, prefix, mappings)
    suggestion = suggest_path_map(movies, series, folders)
    server = info.get("ServerName") if isinstance(info.get("ServerName"), str) else None
    version = info.get("Version") if isinstance(info.get("Version"), str) else None
    label = server or "Jellyfin"
    if version:
        label = f"{label} {version}"
    if user_name and not user_id and detected_id:
        message = f"Connecté à {label}. Un seul utilisateur ({user_name}) : son identifiant est proposé."
    elif user_name:
        message = f"Connecté à {label}, utilisateur {user_name}."
    else:
        message = f"Connecté à {label}."
    if warnings:
        message = f"{message} Les chemins de bibliothèque ne correspondent pas."
    return JellyfinTestOut(
        ok=True,
        server_name=server,
        version=version,
        user_name=user_name,
        user_id=detected_id or (user_id or None),
        path_warnings=warnings,
        suggested_path_map=suggestion,
        libraries=[JellyfinLibraryOut.model_validate(folder) for folder in folders],
        message=message,
    )


@router.post("/webhook", response_model=WebhookOut)
async def jellyfin_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_mediatracker_token: str | None = Header(
        default=None,
        alias=WEBHOOK_TOKEN_HEADER,
        description="Jeton webhook (prioritaire sur le paramètre de requête `token`).",
    ),
    token: str | None = Query(
        default=None,
        description="Jeton webhook (déprécié ; préférez l'en-tête X-MediaTracker-Token).",
    ),
) -> WebhookOut:
    moment = datetime.now(timezone.utc)
    sent, used_query = resolve_webhook_token(x_mediatracker_token, token)
    expected = webhook_secret(db, request.app.state.settings)
    if not webhook_token_valid(expected, sent):
        raise Problem(401, "Non autorisé", "Jeton webhook Jellyfin refusé.")
    if used_query:
        note_webhook_query_token_usage(db, sent, moment)
    try:
        payload = await request.json()
    except Exception as exc:
        raise Problem(422, "Requête invalide", "Corps JSON attendu.") from exc
    if not isinstance(payload, dict):
        raise Problem(422, "Requête invalide", "Corps JSON attendu.")
    kind = str(payload.get("NotificationType") or payload.get("Event") or "")
    scheduled = kind in {"ItemAdded", "ItemUpdated"}
    if scheduled:
        from mediatracker.library.identity_sync import schedule_identity_read

        # The body is not an identity source. The worker re-reads the catalog.
        schedule_identity_read(db, moment)
    updated = apply_webhook(db, request.app.state.settings, payload, moment)
    from mediatracker.core.worker_wake import REASON_API_JELLYFIN, wake_worker

    wake_worker(db, request.app.state.settings, REASON_API_JELLYFIN, commit=False)
    db.commit()
    return WebhookOut(updated=updated, scheduled=scheduled)


def _resolve_user(client: JellyfinClient, user_id: str) -> tuple[str | None, str | None]:
    try:
        users = client.users()
    except JellyfinError:
        return None, None
    try:
        return match_jellyfin_user(users, user_id)
    except JellyfinError as exc:
        if exc.code == "NO_USER" and not (user_id or "").strip():
            return None, None
        raise


def _folders(client: JellyfinClient) -> list[dict]:
    method = getattr(client, "virtual_folders", None)
    if method is None:
        return []
    try:
        raw = method()
    except JellyfinError:
        return []
    folders = []
    for row in raw:
        locations = row.get("Locations") if isinstance(row.get("Locations"), list) else []
        folders.append(
            {
                "name": str(row.get("Name") or "Bibliothèque"),
                "collection_type": str(row.get("CollectionType") or ""),
                "locations": [item for item in locations if isinstance(item, str)],
            }
        )
    return folders


def _sync_time(db: Session) -> datetime | None:
    row = db.get(Setting, LAST_POLL_AT)
    if row is None or not row.value:
        return None
    try:
        moment = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _sync_error(db: Session) -> str | None:
    row = db.get(Setting, LAST_ERROR)
    if row is None or not row.value:
        return None
    return row.value


