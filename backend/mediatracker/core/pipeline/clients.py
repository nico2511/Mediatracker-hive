"""Reused AllDebrid and aria2 clients. A new database or config closes the previous one."""


from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Provider
from mediatracker.integrations.alldebrid_key import effective_alldebrid_key, env_api_key
from mediatracker.security.crypto import decrypt_secret

_CLIENTS: dict[str, tuple] = {}
_REGISTRY = None


def _provider_registry():
    global _REGISTRY
    if _REGISTRY is None:
        from mediatracker.providers.builtin import register_builtins
        from mediatracker.providers.registry import ProviderRegistry

        _REGISTRY = ProviderRegistry()
        register_builtins(_REGISTRY)
    return _REGISTRY


def _client_identity(settings: Settings, row: Provider, plain: dict) -> tuple:
    import hashlib
    import json

    digest = hashlib.sha256(json.dumps(plain, sort_keys=True, default=str).encode()).hexdigest()
    return (settings.database_url, row.id, row.type, digest)


def _close_client(client) -> None:
    close = getattr(client, "close", None)
    if close is not None:
        close()


def _cached_client(kind: str, identity: tuple, factory):
    previous = _CLIENTS.get(kind)
    if previous is not None and previous[0] == identity:
        return previous[1]
    if previous is not None:
        _close_client(previous[1])
    client = factory()
    _CLIENTS[kind] = (identity, client)
    return client


def close_cached_clients() -> None:
    """Close reused AllDebrid and aria2 clients. The worker calls this on shutdown."""
    for _identity, client in list(_CLIENTS.values()):
        _close_client(client)
    _CLIENTS.clear()

def _debrid(db: Session, settings: Settings, injected):
    env = env_api_key(settings)
    stmt = select(Provider).where(Provider.kind == "debrid", Provider.type == "alldebrid")
    if not env:
        stmt = stmt.where(Provider.enabled.is_(True))
    row = db.scalar(stmt)
    if injected is not None:
        return row, injected
    if row is None:
        return None, None
    if not effective_alldebrid_key(settings, db):
        return row, None
    registry = _provider_registry()
    plain = _plain_config(row, settings, registry, db)
    identity = _client_identity(settings, row, plain)
    client = _cached_client("debrid", identity, lambda: registry.instantiate(row.kind, row.type, plain))
    return row, client


def _downloader(db: Session, settings: Settings, injected):
    row = db.scalar(
        select(Provider).where(Provider.kind == "downloader", Provider.type == "aria2", Provider.enabled.is_(True))
    )
    if injected is not None:
        return row, injected
    if row is None:
        return None, None
    registry = _provider_registry()
    plain = _plain_config(row, settings, registry, db)
    identity = _client_identity(settings, row, plain)
    client = _cached_client("downloader", identity, lambda: registry.instantiate(row.kind, row.type, plain))
    return row, client


def _plain_config(row: Provider, settings: Settings, registry, db: Session) -> dict:
    model = registry.get(row.kind, row.type)
    from mediatracker.providers.registry import secret_fields

    names = secret_fields(model.config_model())
    plain = {}
    for key, value in (row.config or {}).items():
        if key in names and isinstance(value, str) and value:
            plain[key] = decrypt_secret(value, settings.secret_key)
        else:
            plain[key] = value
    if row.kind == "debrid" and row.type == "alldebrid":
        plain["api_key"] = effective_alldebrid_key(settings, db)
    return plain

