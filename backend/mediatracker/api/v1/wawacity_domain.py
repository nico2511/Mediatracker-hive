"""Admin API for Wawacity domain state (auto migration with optional propose-only mode)."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.wawacity_domain import (
    DomainValidationError,
    apply_candidate,
    read_domain_state,
    revert_domain,
)

router = APIRouter(prefix="/wawacity/domain", tags=["settings"])


class WawacityDomainOut(BaseModel):
    current: str | None
    candidate: str | None
    previous: str | None


@router.get("", response_model=WawacityDomainOut)
def get_wawacity_domain(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> WawacityDomainOut:
    del _principal
    return WawacityDomainOut(**read_domain_state(db, request.app.state.settings))


@router.post("/apply", response_model=WawacityDomainOut)
def apply_wawacity_domain(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> WawacityDomainOut:
    del _principal
    transport = getattr(request.app.state, "wawacity_domain_transport", None)
    try:
        state = apply_candidate(db, request.app.state.settings, transport=transport)
    except DomainValidationError as exc:
        raise Problem(422, "Requête invalide", exc.message) from exc
    return WawacityDomainOut(**state)


@router.post("/revert", response_model=WawacityDomainOut)
def revert_wawacity_domain(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> WawacityDomainOut:
    del _principal
    try:
        state = revert_domain(db, request.app.state.settings)
    except DomainValidationError as exc:
        raise Problem(422, "Requête invalide", exc.message) from exc
    return WawacityDomainOut(**state)
