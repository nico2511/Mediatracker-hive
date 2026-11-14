from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.cookies import clear_auth_cookies, set_csrf_cookie, set_session_cookie
from mediatracker.api.deps import Principal, client_ip, get_db, get_principal
from mediatracker.api.errors import Problem
from mediatracker.db.models import User
from mediatracker.security.password_policy import validate_new_password
from mediatracker.security.passwords import hash_password, verify_dummy, verify_password
from mediatracker.security.session_epoch import bump_session_epoch

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    username: str


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> UserOut:
    limiter = request.app.state.login_limiter
    ip = client_ip(request)
    keys = (f"ip:{ip}", f"user:{body.username.strip().lower()}")
    if any(limiter.too_many(key) for key in keys):
        wait = max(limiter.retry_after(key) for key in keys)
        raise Problem(
            429,
            "Trop de tentatives",
            "Trop de tentatives de connexion. Le compte est verrouillé pour un moment.",
            headers={"Retry-After": str(wait)},
        )
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(user.password_hash, body.password):
        if user is None:
            verify_dummy(body.password)
        for key in keys:
            limiter.record_failure(key)
        raise Problem(401, "Identifiants refusés", "Nom d'utilisateur ou mot de passe incorrect.")
    for key in keys:
        limiter.reset(key)
    request.state.refresh_session = False
    max_age = request.app.state.settings.session_max_age
    set_session_cookie(response, request.app.state.sessions.dumps(user.id, user.session_epoch), max_age, request)
    set_csrf_cookie(response, max_age, request)
    return UserOut(id=user.id, username=user.username)


@router.post("/logout", status_code=204)
def logout(request: Request, principal: Principal = Depends(get_principal)) -> Response:
    del principal
    request.state.refresh_session = False
    response = Response(status_code=204)
    clear_auth_cookies(response, request)
    return response


@router.get("/me", response_model=UserOut)
def me(principal: Principal = Depends(get_principal)) -> UserOut:
    return UserOut(id=principal.user_id, username=principal.username)


def _refresh_session_state(request: Request, user: User) -> None:
    request.state.session_user_id = user.id
    request.state.session_epoch = user.session_epoch
    request.state.refresh_session = True


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChangeIn,
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Response:
    user = db.get(User, principal.user_id)
    if user is None:
        raise Problem(401, "Non authentifié", "Session invalide.")
    if not verify_password(user.password_hash, body.current_password):
        raise Problem(401, "Identifiants refusés", "Mot de passe actuel incorrect.")
    if body.current_password == body.new_password:
        raise Problem(422, "Requête invalide", "Le nouveau mot de passe doit être différent.")
    validate_new_password(body.new_password, username=user.username)
    user.password_hash = hash_password(body.new_password)
    bump_session_epoch(db, user)
    _refresh_session_state(request, user)
    return Response(status_code=204)


@router.post("/sessions/revoke-others", status_code=204)
def revoke_other_sessions(
    request: Request,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> Response:
    user = db.get(User, principal.user_id)
    if user is None:
        raise Problem(401, "Non authentifié", "Session invalide.")
    bump_session_epoch(db, user)
    _refresh_session_state(request, user)
    return Response(status_code=204)
