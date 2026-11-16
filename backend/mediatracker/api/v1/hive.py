"""Hive T1 : identité et réglages (API authentifiée)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.api.v1 import hive_peer_load, hive_ruches, hive_ui
from mediatracker.hive.identity import (
    hive_enabled_in_db,
    hive_identity_is_lost,
    identity_state_dict,
    reset_hive_identity,
)

router = APIRouter(prefix="/hive", tags=["hive"])
router.include_router(hive_ruches.router)
router.include_router(hive_peer_load.router)
router.include_router(hive_ui.router)


class HiveIdentityOut(BaseModel):
    enabled: bool
    identity_lost: bool = False
    identity_lost_message: str | None = None
    instance_id: str | None = None
    public_key_pem: str | None = None
    name: str | None = None
    public_address: str | None = None
    pull_only: bool = False
    name_document: dict | None = None
    name_signature_b64: str | None = None


@router.get("/identity", response_model=HiveIdentityOut)
def get_hive_identity(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> HiveIdentityOut:
    del _principal
    if not hive_enabled_in_db(db):
        raise Problem(404, "Introuvable", "Hive est désactivé.")
    payload = identity_state_dict(db, request.app.state.settings)
    return HiveIdentityOut.model_validate(payload)


class HiveResetIn(BaseModel):
    """Nouveau nom obligatoire (2–32 caractères)."""

    name: str


@router.post("/identity/reset", status_code=204)
def post_hive_identity_reset(
    request: Request,
    _body: HiveResetIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    if not hive_enabled_in_db(db) and not hive_identity_is_lost(db, request.app.state.settings):
        raise Problem(404, "Introuvable", "Hive est désactivé.")
    try:
        reset_hive_identity(db, request.app.state.settings, new_name=_body.name)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc
    db.commit()
