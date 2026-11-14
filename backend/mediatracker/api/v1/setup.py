"""First-run wizard: account, connection probes, then the first library scan."""

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mediatracker.api.cookies import set_csrf_cookie, set_session_cookie
from mediatracker.api.deps import client_ip, get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.db.models import Setting, User
from mediatracker.integrations.tmdb import TmdbClient, TmdbError
from mediatracker.library.scanner import ScanReport, force_scan
from mediatracker.providers.debrid.alldebrid import AllDebridConfig, AllDebridProvider
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.security.crypto import decrypt_secret
from mediatracker.security.password_policy import validate_new_password
from mediatracker.security.passwords import hash_password
from mediatracker.security.setup_bootstrap import prepare_setup_bootstrap_token, token_matches
from mediatracker.security.ssrf import OutboundUrlRejected, settings_rejection_message, validate_outbound_url_static

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

SETUP_KEY = "system.setup_completed"


class SetupStatus(BaseModel):
    needs_password: bool
    completed: bool


class AccountIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    setup_token: str = ""


class AccountOut(BaseModel):
    id: int
    username: str


class ProbeIn(BaseModel):
    target: str = Field(pattern="^(alldebrid|tmdb|wawacity|homeassistant)$")
    value: str = ""
    secret: str | None = None


class ProbeOut(BaseModel):
    ok: bool
    message: str


class SetupSourcesIn(BaseModel):
    wawacity_base_url: str | None = None
    torrentio_base_url: str | None = None
    comet_base_urls: str | None = None
    free_telecharger_base_url: str | None = None


class SetupSourcesOut(BaseModel):
    saved: list[str]


class SetupDone(BaseModel):
    completed: bool
    seen: int
    matched: int
    review: int
    unmatched: int
    missing: int
    followed: int


@router.get("/status", response_model=SetupStatus)
def setup_status(db: Session = Depends(get_db)) -> SetupStatus:
    count = db.scalar(select(func.count()).select_from(User)) or 0
    return SetupStatus(needs_password=count == 0, completed=_completed(db))


@router.post("/account", response_model=AccountOut, status_code=201)
def create_account(
    body: AccountIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AccountOut:
    count = db.scalar(select(func.count()).select_from(User)) or 0
    if count:
        raise Problem(409, "Déjà configuré", "Un compte existe déjà. Connectez-vous.")
    limiter = request.app.state.login_limiter
    ip = client_ip(request)
    limit_key = f"setup:{ip}"
    if limiter.too_many(limit_key):
        wait = limiter.retry_after(limit_key)
        raise Problem(
            429,
            "Trop de tentatives",
            "Trop de tentatives de création de compte. Réessayez dans un moment.",
            headers={"Retry-After": str(wait)},
        )
    expected = getattr(request.app.state, "setup_bootstrap_token", None)
    if not (expected or "").strip():
        request.app.state.setup_bootstrap_token = prepare_setup_bootstrap_token(db, request.app.state.settings)
        limiter.record_failure(limit_key)
        raise Problem(
            403,
            "Jeton refusé",
            "Jeton d'installation incorrect ou manquant. Consultez les journaux du conteneur ou SETUP_TOKEN.",
        )
    provided = body.setup_token.strip()
    if not token_matches(expected, provided):
        limiter.record_failure(limit_key)
        raise Problem(
            403,
            "Jeton refusé",
            "Jeton d'installation incorrect ou manquant. Consultez les journaux du conteneur ou SETUP_TOKEN.",
        )
    limiter.reset(limit_key)
    validate_new_password(body.password, username=body.username.strip())
    user = User(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    request.app.state.setup_bootstrap_token = None
    request.state.refresh_session = False
    max_age = request.app.state.settings.session_max_age
    set_session_cookie(response, request.app.state.sessions.dumps(user.id, user.session_epoch), max_age, request)
    set_csrf_cookie(response, max_age, request)
    logger.info("Compte créé par l'assistant (%s).", user.username)
    return AccountOut(id=user.id, username=user.username)


@router.post("/sources", response_model=SetupSourcesOut)
def save_setup_sources(
    body: SetupSourcesIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> SetupSourcesOut:
    del _principal
    from mediatracker.providers.sources.free_telecharger import FreeTelechargerConfig, FreeTelechargerSource
    from mediatracker.providers.sources.loading import _write_provider_base_url
    from mediatracker.settings_store import SettingsError, update_settings

    settings = request.app.state.settings
    values: dict[str, str] = {}
    saved: list[str] = []
    for key, raw in (
        ("wawacity_base_url", body.wawacity_base_url),
        ("torrentio_base_url", body.torrentio_base_url),
        ("comet_base_urls", body.comet_base_urls),
    ):
        text = _optional_setup_text(raw)
        if text is not None:
            values[key] = text
    if values:
        try:
            update_settings(db, settings, values)
        except SettingsError as exc:
            raise Problem(422, "Requête invalide", str(exc)) from exc
        saved.extend(values)
    ft_url = _optional_setup_text(body.free_telecharger_base_url)
    if ft_url is not None:
        try:
            FreeTelechargerConfig.model_validate({"base_url": ft_url, "user_agent": ""})
        except ValueError as exc:
            raise Problem(422, "Requête invalide", str(exc)) from exc
        _write_provider_base_url(db, FreeTelechargerSource, ft_url)
        db.commit()
        saved.append("free_telecharger_base_url")
    return SetupSourcesOut(saved=saved)


@router.post("/probe", response_model=ProbeOut)
def probe(
    body: ProbeIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> ProbeOut:
    del _principal
    settings = request.app.state.settings
    if body.target == "alldebrid":
        return _probe_alldebrid(body.value or _secret(db, "alldebrid", settings))
    if body.target == "tmdb":
        return _probe_tmdb(body.value or _secret(db, "tmdb_read_token", settings))
    if body.target == "wawacity":
        return _probe_wawacity(body.value or _text(db, "wawacity_base_url"))
    return _probe_homeassistant(body.value)


@router.post("/complete", response_model=SetupDone)
def complete_setup(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> SetupDone:
    del _principal
    report = force_scan(db, request.app.state.settings)
    _mark_complete(db)
    db.commit()
    return _done(report)


def _probe_alldebrid(api_key: str) -> ProbeOut:
    key = api_key.strip()
    if not key:
        return ProbeOut(ok=False, message="Collez la clé API AllDebrid.")
    provider = AllDebridProvider(AllDebridConfig(api_key=key))
    try:
        health = provider.health()
    finally:
        provider.client.close()
    if health.status == "ok":
        return ProbeOut(ok=True, message=health.message or "AllDebrid répond.")
    return ProbeOut(ok=False, message=health.message or "AllDebrid a refusé la clé.")


def _probe_tmdb(token: str) -> ProbeOut:
    raw = token.strip()
    if not raw:
        return ProbeOut(ok=False, message="Collez le jeton de lecture TMDB.")
    client = TmdbClient(raw)
    try:
        client.ping()
    except TmdbError as exc:
        return ProbeOut(ok=False, message=exc.message)
    finally:
        client.close()
    return ProbeOut(ok=True, message="TMDB répond.")


def _probe_wawacity(url: str) -> ProbeOut:
    raw = url.strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        return ProbeOut(ok=False, message="L'adresse Wawacity doit commencer par http:// ou https://.")
    source = WawacitySource(WawacityConfig(base_url=raw))
    try:
        health = source.health()
    finally:
        source.close()
    if health.code == "CHALLENGE" or health.status == "degraded":
        return ProbeOut(
            ok=False,
            message="Cloudflare bloque la page. Le domaine a peut-être changé, ou il faut un accès sans défi.",
        )
    if health.status != "ok":
        return ProbeOut(ok=False, message=health.message or "Wawacity ne répond pas.")
    return ProbeOut(ok=True, message="Wawacity répond.")


def _probe_homeassistant(url: str) -> ProbeOut:
    raw = url.strip()
    if "/api/webhook/" not in raw or not raw.startswith(("http://", "https://")):
        return ProbeOut(ok=False, message="L'URL doit ressembler à http://hôte:8123/api/webhook/mediatracker.")
    body = b'{"event":"notify.test","title":"MediaTracker","message":"Test de l\'assistant."}'
    try:
        validate_outbound_url_static(raw)
    except OutboundUrlRejected:
        return ProbeOut(ok=False, message=settings_rejection_message())
    try:
        response = httpx.post(
            raw,
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=8.0,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return ProbeOut(ok=False, message="Home Assistant n'a pas répondu. Vérifiez l'hôte et le webhook.")
    if 200 <= response.status_code < 300:
        return ProbeOut(ok=True, message="Home Assistant a accepté le test.")
    return ProbeOut(ok=False, message=f"Home Assistant a répondu HTTP {response.status_code}.")


def _completed(db: Session) -> bool:
    row = db.get(Setting, SETUP_KEY)
    return row is not None and row.value == "1"


def _mark_complete(db: Session) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(Setting, SETUP_KEY)
    if row is None:
        db.add(Setting(key=SETUP_KEY, value="1", is_secret=False, updated_at=now))
        return
    row.value = "1"
    row.updated_at = now


def _done(report: ScanReport) -> SetupDone:
    return SetupDone(
        completed=True,
        seen=report.seen,
        matched=report.matched,
        review=report.review,
        unmatched=report.unmatched,
        missing=report.missing,
        followed=report.followed,
    )


def _optional_setup_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text if text else None


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row is not None and row.value else ""


def _secret(db: Session, key: str, settings) -> str:
    if key == "alldebrid":
        from mediatracker.integrations.alldebrid_key import effective_alldebrid_key

        return effective_alldebrid_key(settings, db)
    raw = _text(db, key)
    if not raw:
        return ""
    return decrypt_secret(raw, settings.secret_key)
