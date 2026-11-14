"""Component status for the Santé screen. Secrets are not included."""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.aria2_unreachable import aria2_down_message, note_reachable, unreachable_minutes
from mediatracker.core.disk import free_bytes, threshold_bytes
from mediatracker.core.library_guard import is_suspended
from mediatracker.core.storage import incomplete_dir, same_filesystem_warning, storage_layout
from mediatracker.db.models import Provider, Webhook
from mediatracker.integrations.alldebrid_key import (
    NEUTRAL_UNREADABLE_MESSAGE,
    db_key_unreadable,
    effective_alldebrid_key,
    merge_alldebrid_api_key,
    provider_row,
)
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError
from mediatracker.integrations.jellyfin_sync import jellyfin_settings
from mediatracker.integrations.jellyfin_webhook_auth import webhook_query_token_warning
from mediatracker.integrations.tmdb import TmdbError
from mediatracker.providers.builtin import register_builtins
from mediatracker.providers.registry import ProviderRegistry, secret_fields
from mediatracker.providers.sources.loading import wawacity_config
from mediatracker.providers.sources.wawacity import WawacitySource
from mediatracker.security.crypto import decrypt_secret


@dataclass(frozen=True)
class Component:
    name: str
    status: str
    message: str


def database_ok(db: Session) -> bool:
    db.execute(text("SELECT 1"))
    return True


def components(db: Session, settings: Settings) -> list[Component]:
    return [
        _aria2(db, settings),
        _alldebrid(db, settings),
        _tmdb(db, settings),
        _jellyfin(db, settings),
        _wawacity(db, settings),
        _notifier(db),
        _disk(db, settings),
        _library(db),
        _volumes(db, settings),
    ]


def _aria2(db: Session, settings: Settings) -> Component:
    row = db.scalar(select(Provider).where(Provider.kind == "downloader", Provider.type == "aria2"))
    if row is None:
        return Component("aria2", "down", "aria2 n'est pas enregistré.")
    now = datetime.now(timezone.utc)
    client = None
    try:
        client = _instantiate(row, settings, db)
        health = client.health()
    except Exception:
        return Component("aria2", "down", aria2_down_message(db, settings, now))
    finally:
        _close(client)
    if health.status == "down":
        if health.code == "UNREACHABLE" or unreachable_minutes(db, now) is not None:
            return Component("aria2", "down", aria2_down_message(db, settings, now))
        return Component("aria2", "down", health.message or "aria2 est injoignable.")
    note_reachable(db, settings, now)
    return Component("aria2", health.status, health.message or "aria2")


def _alldebrid(db: Session, settings: Settings) -> Component:
    if db_key_unreadable(settings, db):
        return Component("alldebrid", "down", NEUTRAL_UNREADABLE_MESSAGE)
    key = effective_alldebrid_key(settings, db)
    if not key:
        return Component("alldebrid", "skipped", "Clé AllDebrid absente.")
    row = provider_row(db)
    if row is None:
        return Component("alldebrid", "skipped", "Clé AllDebrid absente.")
    client = None
    try:
        client = _instantiate(row, settings, db)
        health = client.health()
    except Exception:
        return Component("alldebrid", "down", "AllDebrid est injoignable.")
    finally:
        _close(client)
    return Component("alldebrid", health.status, health.message or "AllDebrid")


def _tmdb(db: Session, settings: Settings) -> Component:
    token = _secret(db, "tmdb_read_token", settings)
    if not token:
        return Component("tmdb", "skipped", "Jeton TMDB absent.")
    from mediatracker.integrations.tmdb import shared_tmdb

    client = shared_tmdb(token)
    try:
        client.ping()
    except TmdbError as exc:
        return Component("tmdb", "down", exc.message)
    return Component("tmdb", "ok", "TMDB répond.")


def _jellyfin(db: Session, settings: Settings) -> Component:
    query_warning = webhook_query_token_warning(db)
    url, api_key, user_id, _prefix, _path_map = jellyfin_settings(db, settings)
    if not url or not api_key:
        if query_warning:
            return Component("jellyfin", "degraded", query_warning)
        return Component("jellyfin", "skipped", "Jellyfin n'est pas configuré.")
    client = JellyfinClient(url, api_key, user_id or None, timeout=8.0)
    try:
        info = client.system_info()
    except JellyfinError as exc:
        message = exc.message
        if query_warning:
            message = f"{message} {query_warning}"
        status = "degraded" if query_warning else "down"
        return Component("jellyfin", status, message)
    finally:
        client.close()
    name = info.get("ServerName") if isinstance(info.get("ServerName"), str) else "Jellyfin"
    message = f"Connecté à {name}."
    if query_warning:
        return Component("jellyfin", "degraded", f"{message} {query_warning}")
    return Component("jellyfin", "ok", message)


def _wawacity(db: Session, settings: Settings) -> Component:
    config = wawacity_config(db, settings)
    if not config.base_url:
        return Component("wawacity", "skipped", "Domaine Wawacity absent.")
    source = WawacitySource(config)
    try:
        health = source.health()
    except Exception:
        return Component("wawacity", "down", "Wawacity est injoignable.")
    finally:
        source.close()
    return Component("wawacity", health.status, health.message or "Wawacity")


def _notifier(db: Session) -> Component:
    count = db.scalar(select(func.count()).select_from(Webhook).where(Webhook.enabled.is_(True))) or 0
    if count == 0:
        return Component("notifier", "skipped", "Aucune cible de notification.")
    return Component("notifier", "ok", f"{count} cible(s) active(s).")


def _volumes(db: Session, settings: Settings) -> Component:
    layout = storage_layout(db, settings)
    warning = same_filesystem_warning(layout)
    if warning:
        return Component("volumes", "degraded", warning)
    return Component("volumes", "ok", "Films, séries, téléchargements et corbeille sont sur le même disque.")


def _library(db: Session) -> Component:
    if is_suspended(db):
        return Component(
            "library",
            "degraded",
            "Scan bibliothèque suspendu : la bibliothèque semble vide ou trop de fichiers manquent.",
        )
    return Component("library", "ok", "Bibliothèque accessible.")


def _disk(db: Session, settings: Settings) -> Component:
    target = incomplete_dir(storage_layout(db, settings))
    free = free_bytes(target, None)
    if free is None:
        return Component("disk", "down", "Espace disque illisible.")
    limit = threshold_bytes(db, settings)
    free_gb = free / (1024 * 1024 * 1024)
    if limit and free < limit:
        return Component("disk", "down", f"{free_gb:.1f} Go libres, sous le seuil.")
    if limit:
        return Component("disk", "ok", f"{free_gb:.1f} Go libres.")
    return Component("disk", "ok", f"{free_gb:.1f} Go libres. Seuil désactivé.")


def _instantiate(row: Provider, settings: Settings, db: Session):
    registry = ProviderRegistry()
    register_builtins(registry)
    model = registry.get(row.kind, row.type)
    names = secret_fields(model.config_model())
    plain = {}
    for key, value in (row.config or {}).items():
        if key in names and isinstance(value, str) and value:
            plain[key] = decrypt_secret(value, settings.secret_key)
        else:
            plain[key] = value
    if row.kind == "debrid" and row.type == "alldebrid":
        plain = merge_alldebrid_api_key(plain, settings, db)
    return registry.instantiate(row.kind, row.type, plain)


def _close(client) -> None:
    if client is None:
        return
    if hasattr(client, "close"):
        client.close()
        return
    inner = getattr(client, "client", None)
    if inner is not None and hasattr(inner, "close"):
        inner.close()


def _text(db: Session, key: str) -> str:
    from mediatracker.db.models import Setting

    row = db.get(Setting, key)
    return row.value if row is not None and row.value else ""


def _secret(db: Session, key: str, settings: Settings) -> str:
    raw = _text(db, key)
    if not raw:
        return ""
    return decrypt_secret(raw, settings.secret_key)
