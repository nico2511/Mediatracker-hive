"""AllDebrid purge and account linking (PIN / manual key)."""

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.debrid_purge import PurgePacer, mark_purged, purge_account, summary, targets_for_scope
from mediatracker.core.pipeline import _debrid
from mediatracker.integrations.alldebrid_account import (
    account_snapshot,
    cancel_pin,
    clear_db_api_key,
    poll_pin,
    save_manual_key,
    start_pin,
)
from mediatracker.providers.debrid.alldebrid import AllDebridError

router = APIRouter(prefix="/integrations/alldebrid", tags=["alldebrid"])


class PurgeIn(BaseModel):
    scope: str = Field(default="mediatracker", pattern="^(mediatracker|all)$")
    confirm: bool = False


class PurgeOut(BaseModel):
    saved: int
    history: int
    magnets: int
    history_wiped: bool
    message: str


class PinView(BaseModel):
    pin: str | None = None
    user_url: str | None = None
    expires_in: int | None = None


class AccountOut(BaseModel):
    connected: bool
    source: Literal["env", "db", "none"]
    env_locked: bool
    pin_phase: Literal["idle", "pending", "expired"]
    pin: PinView | None = None
    pin_message: str | None = None
    pin_error: str | None = None
    env_notice: str | None = None
    account_notice: str | None = None


class ManualKeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=256)


def _account_out(data: dict[str, Any]) -> AccountOut:
    pin_raw = data.get("pin")
    pin = None
    if isinstance(pin_raw, dict):
        pin = PinView(
            pin=pin_raw.get("pin"),
            user_url=pin_raw.get("user_url"),
            expires_in=pin_raw.get("expires_in"),
        )
    return AccountOut(
        connected=bool(data.get("connected")),
        source=data.get("source") or "none",
        env_locked=bool(data.get("env_locked")),
        pin_phase=data.get("pin_phase") or "idle",
        pin=pin,
        pin_message=data.get("pin_message"),
        pin_error=data.get("pin_error"),
        env_notice=data.get("env_notice"),
        account_notice=data.get("account_notice"),
    )


def _env_locked(settings) -> Problem | None:
    from mediatracker.integrations.alldebrid_key import env_api_key

    if env_api_key(settings):
        return Problem(
            409,
            "AllDebrid",
            "Clé définie dans la configuration du serveur. Connexion gérée hors interface.",
        )
    return None


@router.get("/account", response_model=AccountOut)
def read_alldebrid_account(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> AccountOut:
    del _principal
    settings = request.app.state.settings
    return _account_out(account_snapshot(settings, db))


@router.post("/account/pin/start", response_model=AccountOut)
def begin_alldebrid_pin(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> AccountOut:
    del _principal
    settings = request.app.state.settings
    blocked = _env_locked(settings)
    if blocked is not None:
        raise blocked
    try:
        start_pin(db, settings)
    except AllDebridError as exc:
        raise Problem(502, "AllDebrid", exc.message) from exc
    return _account_out(account_snapshot(settings, db, pin_phase="pending"))


@router.post("/account/pin/poll", response_model=AccountOut)
def poll_alldebrid_pin(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> AccountOut:
    del _principal
    settings = request.app.state.settings
    return _account_out(poll_pin(db, settings))


@router.post("/account/pin/cancel", response_model=AccountOut)
def cancel_alldebrid_pin(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> AccountOut:
    del _principal
    settings = request.app.state.settings
    blocked = _env_locked(settings)
    if blocked is not None:
        raise blocked
    cancel_pin(db)
    return _account_out(account_snapshot(settings, db))


@router.post("/account/disconnect", response_model=AccountOut)
def disconnect_alldebrid_account(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> AccountOut:
    del _principal
    settings = request.app.state.settings
    blocked = _env_locked(settings)
    if blocked is not None:
        raise blocked
    clear_db_api_key(db, settings)
    return _account_out(account_snapshot(settings, db))


@router.post("/account/manual", response_model=AccountOut)
def save_alldebrid_manual_key(
    body: ManualKeyIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> AccountOut:
    del _principal
    settings = request.app.state.settings
    blocked = _env_locked(settings)
    if blocked is not None:
        raise blocked
    try:
        save_manual_key(db, settings, body.api_key)
    except AllDebridError as exc:
        raise Problem(422, "AllDebrid", exc.message) from exc
    except ValueError as exc:
        if str(exc) == "missing_key":
            raise Problem(422, "Requête invalide", "Clé AllDebrid requise.") from exc
        raise
    return _account_out(account_snapshot(settings, db))


@router.post("/purge", response_model=PurgeOut)
def purge_alldebrid(
    body: PurgeIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> PurgeOut:
    """Delete MediaTracker links, or the whole account when ``confirm`` is set."""
    del _principal
    if body.scope == "all" and not body.confirm:
        raise Problem(
            422,
            "Confirmation requise",
            "« Tout purger » vide l'historique récent AllDebrid. Confirmez l'action.",
        )
    _row, client = _debrid(db, request.app.state.settings, None)
    if client is None or not hasattr(client, "delete_saved_links"):
        raise Problem(422, "AllDebrid", "AllDebrid n'est pas activé.")
    pace = PurgePacer()
    try:
        saved, history, magnets, wipe = targets_for_scope(db, client, scope=body.scope, pacer=pace)
    except AllDebridError as exc:
        raise Problem(502, "AllDebrid", exc.message) from exc
    report = purge_account(
        client,
        saved=saved,
        history=history,
        magnets=magnets,
        wipe_history=wipe,
        pacer=pace,
    )
    if report.error is None:
        mark_purged(db, datetime.now(timezone.utc))
        db.commit()
        from mediatracker.core.precheck import clear_known_files_cache

        clear_known_files_cache()
    return PurgeOut(
        saved=report.saved,
        history=report.history,
        magnets=report.magnets,
        history_wiped=report.history_wiped,
        message=summary(report),
    )
