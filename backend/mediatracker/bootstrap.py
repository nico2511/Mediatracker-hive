"""First-boot seed: admin user, settings, manual source, AllDebrid instance."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.errors import Problem
from mediatracker.config import Settings, get_settings
from mediatracker.core.aria2_session import prune_aria2_session_at_startup
from mediatracker.core.incomplete import prune_incomplete_orphans_at_startup
from mediatracker.core.installed_version import observe_installed_version
from mediatracker.core.quality_profile import ensure_default_profile
from mediatracker.core.storage import ensure_runtime_dirs, log_filesystem_layout
from mediatracker.db.migrate import run_migrations
from mediatracker.db.models import Provider, Setting, User
from mediatracker.db.session import create_session_factory, session_scope
from mediatracker.providers.downloaders.aria2 import ARIA2_DEFAULT_RPC_URL
from mediatracker.security.crypto import decrypt_secret, encrypt_secret
from mediatracker.security.logging import configure_logging
from mediatracker.security.password_policy import validate_new_password
from mediatracker.security.passwords import hash_password
from mediatracker.settings_store import PROVIDERS_SEEDED_KEY, ensure_settings

logger = logging.getLogger(__name__)


def ensure_admin(db: Session, settings: Settings) -> None:
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    if user is not None:
        return
    password = settings.admin_password or ""
    if not password:
        logger.info("Aucun compte : l'assistant de premier démarrage créera le mot de passe.")
        return
    try:
        validate_new_password(password, username=settings.admin_username)
    except Problem as exc:
        logger.error(
            "ADMIN_PASSWORD refusé (%s) : le compte administrateur n'a pas été créé.",
            exc.detail,
        )
        return
    db.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(password),
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    logger.info("Compte administrateur créé (%s).", settings.admin_username)


def ensure_providers(db: Session, settings: Settings) -> None:
    if db.get(Setting, PROVIDERS_SEEDED_KEY) is not None:
        _ensure_aria2(db, settings)
        _ensure_registered_sources(db)
        return
    now = datetime.now(timezone.utc)
    db.add(
        Provider(
            kind="source",
            type="manual",
            name="Manuel",
            enabled=True,
            priority=0,
            config={},
            builtin=True,
            created_at=now,
        )
    )
    api_key = (settings.alldebrid_api_key or "").strip()
    stored = encrypt_secret(api_key, settings.secret_key) if api_key else ""
    db.add(
        Provider(
            kind="debrid",
            type="alldebrid",
            name="AllDebrid",
            enabled=bool(api_key),
            priority=0,
            config={"api_key": stored},
            builtin=False,
            created_at=now,
        )
    )
    db.add(Setting(key=PROVIDERS_SEEDED_KEY, value="1", is_secret=False, updated_at=now))
    db.commit()
    _ensure_aria2(db, settings)
    _ensure_registered_sources(db)


def _ensure_aria2(db: Session, settings: Settings) -> None:
    existing = db.scalar(select(Provider).where(Provider.kind == "downloader", Provider.type == "aria2"))
    if existing is not None:
        return
    secret_row = db.get(Setting, "aria2_rpc_secret")
    secret = ""
    if secret_row is not None and secret_row.value:
        secret = decrypt_secret(secret_row.value, settings.secret_key)
    stored = encrypt_secret(secret, settings.secret_key) if secret else ""
    db.add(
        Provider(
            kind="downloader",
            type="aria2",
            name="aria2",
            enabled=True,
            priority=0,
            config={"rpc_url": ARIA2_DEFAULT_RPC_URL, "rpc_secret": stored},
            builtin=True,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _ensure_registered_sources(db: Session) -> None:
    """One row per registered download source. An existing URL in settings is copied, never dropped."""
    from mediatracker.providers.sources.hive import HiveProvider
    from mediatracker.providers.sources.loading import _download_classes, stored_source_url

    now = datetime.now(timezone.utc)
    _ensure_source_provider_row(db, HiveProvider, now=now)
    for cls in _download_classes():
        if "base_url" not in cls.config_model().model_fields:
            continue
        legacy = stored_source_url(db, cls.type)
        existing = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == cls.type))
        if existing is None:
            config: dict = {}
            if legacy:
                config = _config_with_base_url(cls, {}, legacy)
            db.add(
                Provider(
                    kind="source",
                    type=cls.type,
                    name=cls.type[:1].upper() + cls.type[1:],
                    enabled=True,
                    priority=int(getattr(cls, "download_rank", 1000)),
                    config=config,
                    builtin=True,
                    created_at=now,
                )
            )
            db.commit()
            continue
        current = ""
        if isinstance(existing.config, dict):
            current = str(existing.config.get("base_url") or "").strip()
        if current or not legacy:
            continue
        existing.config = _config_with_base_url(cls, dict(existing.config or {}), legacy)
        db.commit()


def _ensure_source_provider_row(db: Session, cls: type, *, now: datetime) -> None:
    existing = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == cls.type))
    if existing is not None:
        return
    db.add(
        Provider(
            kind="source",
            type=cls.type,
            name="Hive",
            enabled=True,
            priority=int(getattr(cls, "download_rank", 1000)),
            config={},
            builtin=True,
            created_at=now,
        )
    )
    db.commit()


def _config_with_base_url(cls: type, config: dict, url: str) -> dict:
    fields = set(cls.config_model().model_fields)
    clean = {key: value for key, value in config.items() if key in fields}
    clean["base_url"] = url
    try:
        validated = cls.config_model().model_validate(clean)
    except Exception:
        return config
    stored = dict(config)
    stored["base_url"] = validated.base_url
    return stored


def ensure_ready(db: Session, settings: Settings) -> None:
    ensure_admin(db, settings)
    ensure_settings(db, settings)
    observe_installed_version(db, settings)
    ensure_default_profile(db)
    ensure_providers(db, settings)
    ensure_runtime_dirs(db, settings)
    log_filesystem_layout(db, settings)
    from mediatracker.library.repair import repair_crossed_series

    repair_crossed_series(db, settings)
    from mediatracker.core.keep import migrate_cleanup_auto_default

    migrate_cleanup_auto_default(db)
    from mediatracker.core.catalog_french_merge import run_catalog_french_migration_safe
    from mediatracker.core.catalog_poster import migrate_tmdb_poster_cache_once

    merged = run_catalog_french_migration_safe(db)
    if merged:
        logger.info("Catalogue : %s entrée(s) fusionnée(s) (suffixe FRENCH).", merged)
    purged = migrate_tmdb_poster_cache_once(db)
    if purged:
        logger.info("Cache TMDB : %s entrée(s) d'affiche purgée(s) (v2).", purged)
    db.commit()
    from mediatracker.core.tmdb.wire_integrations import wire_jellyfin_integrations

    wire_jellyfin_integrations()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    run_migrations(settings.database_url)
    _engine, factory = create_session_factory(settings)
    with session_scope(factory) as db:
        ensure_ready(db, settings)
        prune_aria2_session_at_startup(db, settings)
        prune_incomplete_orphans_at_startup(db, settings)
    logger.info("Initialisation terminée.")


if __name__ == "__main__":
    main()
