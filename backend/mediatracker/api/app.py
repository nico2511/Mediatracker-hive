"""FastAPI application. OpenAPI docs require a session; /healthz stays public."""

import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, Response

from mediatracker.api.cookies import set_csrf_cookie, set_session_cookie
from mediatracker.api.deps import get_db, get_principal
from mediatracker.api.errors import Problem, problem_response, public_validation_errors
from mediatracker.api.hive_router import mount_hive_router
from mediatracker.api.v1.router import router as v1_router
from mediatracker.bootstrap import ensure_ready
from mediatracker.config import Settings, get_settings
from mediatracker.core.metrics import HttpMetricsMiddleware, MetricsCollector
from mediatracker.db.migrate import run_migrations
from mediatracker.db.models import Setting
from mediatracker.db.session import (
    HTTP_SQLITE_RETRY_AFTER_SECONDS,
    create_session_factory,
    is_sqlite_locked,
    session_scope,
)
from mediatracker.health_report import database_ok
from mediatracker.hive.identity import HIVE_ENABLED_KEY, hive_identity_is_lost
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.providers.builtin import register_builtins
from mediatracker.providers.registry import ProviderRegistry
from mediatracker.security.headers import SecurityHeadersMiddleware
from mediatracker.security.logging import configure_logging
from mediatracker.security.rate_limit import LoginRateLimiter
from mediatracker.security.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionSigner
from mediatracker.security.setup_bootstrap import prepare_setup_bootstrap_token

logger = logging.getLogger(__name__)

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_CSRF_EXEMPT = frozenset({"/api/v1/auth/login", "/api/v1/setup/account"})


class HealthzOut(BaseModel):
    status: str = "ok"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rejected = _csrf_rejection(request)
        if rejected is not None:
            return rejected
        response = await call_next(request)
        _refresh_session(request, response)
        return response


def _csrf_rejection(request: Request) -> Response | None:
    if request.method not in _UNSAFE or request.url.path in _CSRF_EXEMPT:
        return None
    if request.url.path.startswith("/hive/"):
        return None
    if request.url.path.startswith("/hooks/"):
        return None
    if request.url.path == "/api/v1/integrations/jellyfin/webhook":
        return None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return None
    if SESSION_COOKIE not in request.cookies:
        return None
    cookie = request.cookies.get(CSRF_COOKIE, "")
    sent = request.headers.get(CSRF_HEADER, "")
    if not cookie or not sent or len(cookie) != len(sent) or not secrets.compare_digest(cookie, sent):
        return problem_response(
            request,
            403,
            "CSRF refusé",
            "Jeton CSRF manquant ou invalide.",
        )
    return None


def _refresh_session(request: Request, response: Response) -> None:
    if not getattr(request.state, "refresh_session", False):
        return
    user_id = getattr(request.state, "session_user_id", None)
    if not user_id:
        return
    epoch = getattr(request.state, "session_epoch", 0)
    max_age = request.app.state.settings.session_max_age
    set_session_cookie(response, request.app.state.sessions.dumps(user_id, epoch), max_age, request)
    if CSRF_COOKIE not in request.cookies:
        set_csrf_cookie(response, max_age, request)


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(
        settings.log_level,
        Path(settings.config_dir) / "logs",
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    registry = ProviderRegistry()
    register_builtins(registry)
    engine, factory = create_session_factory(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_migrations(app.state.settings.database_url)
        with session_scope(app.state.session_factory) as db:
            ensure_ready(db, app.state.settings)
            app.state.setup_bootstrap_token = prepare_setup_bootstrap_token(db, app.state.settings)
            from mediatracker.hive.transfer_runtime import apply_hive_send_bandwidth

            apply_hive_send_bandwidth(app, db)
            from datetime import datetime, timezone

            from mediatracker.hive.signal_policy import maybe_catchup_cherche_for_followed

            maybe_catchup_cherche_for_followed(db, app.state.settings, now=datetime.now(timezone.utc))
            db.commit()
        from mediatracker.integrations.jellyfin_db_sessions import purge_stale_sessions

        purge_stale_sessions(Path(app.state.settings.config_dir))
        yield
        from mediatracker.core.outbound_http import shutdown_outbound_integrations

        shutdown_outbound_integrations()
        app.state.engine.dispose()

    app = FastAPI(
        title="MediaTracker",
        version="2.0.0",
        summary="API de suivi et de téléchargement de médias.",
        description=(
            "API LAN de MediaTracker v2. L'interface web n'est qu'un client. "
            "Les requêtes mutantes authentifiées par cookie envoient l'en-tête "
            f"{CSRF_HEADER} recopié depuis le cookie {CSRF_COOKIE}. "
            "Les jetons Bearer n'ont pas de CSRF. "
            "Les liens directs AllDebrid ne sont pas persistés."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.registry = registry
    app.state.sessions = SessionSigner(settings.secret_key, settings.session_max_age)
    app.state.login_limiter = LoginRateLimiter(
        limit=settings.login_rate_limit,
        window_seconds=settings.login_rate_window_seconds,
        lockout_seconds=settings.login_lockout_seconds,
    )
    _peer_limit = 30
    try:
        _peer_limit = max(1, int(os.environ.get("HIVE_SANDBOX_PEER_RATE_LIMIT", "30")))
    except ValueError:
        _peer_limit = 30
    app.state.hive_peer_limiter = PeerRateLimiter(limit=_peer_limit, window_seconds=60.0)
    app.state.hive_transfer_parallel = TransferParallelGate()
    app.state.hive_send_bandwidth = TransferBandwidthLimiter(bps=0)
    app.state.setup_bootstrap_token = None
    metrics = MetricsCollector()
    app.state.metrics = metrics
    if settings.public_url:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.public_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(
        HttpMetricsMiddleware,
        registry=metrics.http,
        on_request_end=metrics.lazy_tick,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    class HiveSwitchMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if path != "/hive" and not path.startswith("/hive/"):
                return await call_next(request)
            session = request.app.state.session_factory()
            try:
                row = session.get(Setting, HIVE_ENABLED_KEY)
                hive_on = (row.value or "").strip().lower() == "true" if row is not None else False
                allow_lost_reset = (
                    request.method == "POST"
                    and path.rstrip("/") == "/hive/identity/reset"
                    and hive_identity_is_lost(session, request.app.state.settings)
                )
            finally:
                session.close()
            if not hive_on and not allow_lost_reset:
                return PlainTextResponse("Not Found", status_code=404)
            return await call_next(request)

    app.add_middleware(HiveSwitchMiddleware)

    mount_hive_router(app)

    app.include_router(v1_router)
    from mediatracker.core.tmdb.wire_integrations import wire_jellyfin_integrations

    wire_jellyfin_integrations()

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def pwa_manifest():
        body: dict[str, object] = {
            "name": "MediaTracker",
            "short_name": "MediaTracker",
            "display": "standalone",
            "start_url": "/",
            "scope": "/",
            "lang": "fr",
            "background_color": "#141618",
            "theme_color": "#141618",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "/icon-512-maskable.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
        if settings.public_url:
            body["id"] = f"{settings.public_url}/"
        return JSONResponse(body, media_type="application/manifest+json")

    @app.get("/healthz", tags=["health"])
    def healthz(db=Depends(get_db)):
        """Liveness publique pour Docker. Aucune version ni détail."""
        try:
            database_ok(db)
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return HealthzOut()

    @app.get("/api/docs", include_in_schema=False)
    def swagger_ui(request: Request, db=Depends(get_db)):
        try:
            get_principal(request, db)
        except Problem:
            return RedirectResponse(url="/login", status_code=302)
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="MediaTracker API")

    @app.get("/api/redoc", include_in_schema=False)
    def redoc_ui(request: Request, db=Depends(get_db)):
        try:
            get_principal(request, db)
        except Problem:
            return RedirectResponse(url="/login", status_code=302)
        return get_redoc_html(openapi_url="/api/openapi.json", title="MediaTracker API")

    @app.get("/api/openapi.json", include_in_schema=False)
    def openapi_json(request: Request, _principal=Depends(get_principal)):
        del _principal
        return JSONResponse(request.app.openapi())

    @app.exception_handler(Problem)
    async def problem_handler(request: Request, exc: Problem):
        return problem_response(
            request,
            exc.status,
            exc.title,
            exc.detail,
            headers=exc.headers,
            extra=exc.extra,
            type_=exc.type,
        )

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(OperationalError)
    async def sqlite_locked_handler(request: Request, exc: OperationalError):
        if not is_sqlite_locked(exc):
            raise exc
        return problem_response(
            request,
            503,
            "Base occupée",
            "La base est momentanément occupée. Réessayez dans un instant.",
            headers={"Retry-After": str(HTTP_SQLITE_RETRY_AFTER_SECONDS)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return problem_response(
            request,
            422,
            "Requête invalide",
            "Le corps ou les paramètres sont invalides.",
            extra={"errors": public_validation_errors(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        correlation_id = uuid.uuid4().hex[:12]
        logger.exception("Erreur API non gérée (réf. %s).", correlation_id)
        try:
            with session_scope(request.app.state.session_factory) as journal_db:
                from mediatracker.events import record_event
                from mediatracker.security.logging import redact

                record_event(
                    journal_db,
                    "system.error",
                    level="error",
                    payload={
                        "route": request.url.path,
                        "method": request.method,
                        "exception_type": type(exc).__name__,
                        "exception_message": redact(str(exc) or type(exc).__name__),
                        "correlation_id": correlation_id,
                        "level": "error",
                    },
                )
                journal_db.commit()
        except Exception:
            logger.exception("Écriture Journal system.error échouée (réf. %s).", correlation_id)
        return problem_response(
            request,
            500,
            "Erreur serveur",
            f"Une erreur interne s'est produite. Consultez le Journal (réf. {correlation_id}).",
            extra={"correlation_id": correlation_id},
        )

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        components["securitySchemes"] = {
            "bearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "mt_<prefix>_<secret>",
                "description": "Jeton personnel. Pas de CSRF.",
            },
            "session": {
                "type": "apiKey",
                "in": "cookie",
                "name": SESSION_COOKIE,
                "description": "Cookie de session de l'interface. CSRF requis pour les requêtes mutantes.",
            },
        }
        app.openapi_schema = schema
        return schema

    app.openapi = openapi

    spa = _spa_dir()
    if spa is not None:
        app.mount("/", SPAStaticFiles(directory=spa, html=True), name="spa")
    return app


def _spa_dir() -> Path | None:
    candidates = [
        Path("/app/frontend/dist"),
        Path(__file__).resolve().parents[3] / "frontend" / "dist",
    ]
    for path in candidates:
        if (path / "index.html").is_file():
            return path
    return None


def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
