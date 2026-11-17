"""Build the remote sources the poller is allowed to call.

The core poller receives the objects. It does not import a site module.
Which site opens is decided by provider capabilities, not by a type name.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.source_poll import close_source, maybe_poll_sources, next_due
from mediatracker.db.models import Provider, Setting
from mediatracker.providers.sources.wawacity import DEFAULT_USER_AGENT, WawacityConfig

_REGISTRY = None


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        from mediatracker.providers.builtin import register_builtins
        from mediatracker.providers.registry import ProviderRegistry

        _REGISTRY = ProviderRegistry()
        register_builtins(_REGISTRY)
    return _REGISTRY


def _with_capability(db: Session, capability: str) -> list[tuple[Provider, object]]:
    from mediatracker.providers.sources.registry import for_type

    rows = list(
        db.scalars(
            select(Provider)
            .where(Provider.kind == "source", Provider.enabled.is_(True))
            .order_by(Provider.priority, Provider.id)
        ).all()
    )
    found: list[tuple[Provider, object]] = []
    for row in rows:
        entry = for_type(row.type)
        if entry is None:
            continue
        if capability not in getattr(entry, "capabilities", frozenset()):
            continue
        found.append((row, entry))
    return found


def poll_if_due(db: Session, settings: Settings, **kwargs):
    """Open a links source only when a media is actually due, then close it."""
    commit = kwargs.pop("commit", True)
    now = kwargs.get("now")
    moment = now or datetime.now(timezone.utc)

    def _run():
        if kwargs.get("sources") is None and next_due(db, settings, moment, commit=commit) is None:
            return None
        owned = kwargs.get("sources") is None
        sources = kwargs.get("sources")
        if sources is None:
            from mediatracker.hive.source_poll import poll_follow_respecting_order

            return poll_follow_respecting_order(db, settings, now=moment, rng=kwargs.get("rng"), commit=commit)
        try:
            return maybe_poll_sources(
                db,
                settings,
                now=moment,
                sources=sources,
                rng=kwargs.get("rng"),
                commit=commit,
            )
        finally:
            if owned:
                for _provider_id, source in sources:
                    close_source(source)

    return _run()


def any_latest_source_usable(db: Session, settings: Settings) -> bool:
    """True si au moins une source « latest » est activée et utilisable (sans ouvrir de client)."""
    from mediatracker.core.catalog_source_visibility import any_latest_catalog_usable

    return any_latest_catalog_usable(db, settings)


def sources_for_catalog(db: Session, settings: Settings) -> list[tuple[int, str, object]]:
    """Enabled sources that publish a latest-releases feed. They share the poll pacer and cache."""
    from mediatracker.core.source_availability import source_usable

    delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
    ttl = _int_setting(db, "source_cache_seconds", settings.source_cache_seconds)
    opened: list[tuple[int, str, object]] = []
    for row, entry in _with_capability(db, "latest"):
        if not source_usable(db, settings, row.type):
            continue
        opener = getattr(entry, "open_cached", None) or getattr(entry, "open_poll", None)
        if opener is None:
            continue
        source = opener(db, settings, pacer=_shared_pacer(float(delay)), cache=_shared_cache(float(ttl)))
        if source is None:
            continue
        if "latest" not in getattr(source, "capabilities", frozenset()):
            close_source(source)
            continue
        opened.append((row.id, row.type, source))
    return opened


def remote_links_source(db: Session, settings: Settings, type_name: str) -> tuple[int, object] | None:
    from mediatracker.core.source_availability import source_usable

    if not source_usable(db, settings, type_name):
        return None
    delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
    ttl = _int_setting(db, "source_cache_seconds", settings.source_cache_seconds)
    for row, entry in _with_capability(db, "links"):
        if row.type != type_name:
            continue
        opener = getattr(entry, "open_poll", None)
        if opener is None:
            continue
        source = opener(db, settings, pacer=_shared_pacer(float(delay)), cache=_shared_cache(float(ttl)))
        if source is None:
            continue
        return row.id, source
    return None


def enabled_remote_sources(db: Session, settings: Settings) -> list[tuple[int, object]]:
    from mediatracker.core.source_availability import source_usable

    delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
    ttl = _int_setting(db, "source_cache_seconds", settings.source_cache_seconds)
    opened: list[tuple[int, object]] = []
    for row, entry in _with_capability(db, "links"):
        if not source_usable(db, settings, row.type):
            continue
        opener = getattr(entry, "open_poll", None)
        if opener is None:
            continue
        source = opener(db, settings, pacer=_shared_pacer(float(delay)), cache=_shared_cache(float(ttl)))
        if source is None:
            continue
        opened.append((row.id, source))
    return opened


def wawacity_config(db: Session, settings: Settings) -> WawacityConfig:
    base = source_base_url(db, settings, "wawacity")
    agent = _text(db, "wawacity_user_agent") or (settings.wawacity_user_agent or "") or DEFAULT_USER_AGENT
    return WawacityConfig(base_url=base, user_agent=agent)


def download_source_types() -> tuple[str, ...]:
    """Registered sources that can feed a download, in ``download_rank`` order."""
    return tuple(cls.type for cls in _download_classes())


def download_source_rank(type_name: str) -> int:
    for cls in _download_classes():
        if cls.type == type_name:
            return int(getattr(cls, "download_rank", 1000))
    return 1000


def source_base_url(db: Session, settings: Settings, type_name: str) -> str:
    """Provider config, then the legacy setting, then the environment default.

    An empty config does not hide a URL still stored in ``wawacity_base_url``
    or ``comet_base_url``. Those rows are never deleted.
    """
    if type_name == "comet":
        from mediatracker.providers.sources.comet_urls import resolve_comet_base_urls

        urls = resolve_comet_base_urls(db, settings)
        return urls[0] if urls else ""
    if type_name == "stremio_addons":
        from mediatracker.providers.sources.stremio_addons_config import enabled_stremio_addons

        entries = enabled_stremio_addons(db, settings)
        return entries[0].url if entries else ""
    if type_name == "torrentio":
        key = f"{type_name}_base_url"
        row = db.get(Setting, key)
        if row is not None:
            return (row.value or "").strip().rstrip("/")
        provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == type_name))
        if provider is not None and isinstance(provider.config, dict) and "base_url" in provider.config:
            return str(provider.config.get("base_url") or "").strip().rstrip("/")
        fallback = getattr(settings, key, None)
        if isinstance(fallback, str) and fallback.strip():
            return fallback.strip().rstrip("/")
        return ""
    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == type_name))
    if provider is not None and isinstance(provider.config, dict):
        configured = str(provider.config.get("base_url") or "").strip()
        if configured:
            return configured.rstrip("/")
    key = f"{type_name}_base_url"
    stored = _text(db, key).strip()
    if stored:
        return stored.rstrip("/")
    fallback = getattr(settings, key, None)
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip().rstrip("/")
    return ""


def stored_source_url(db: Session, type_name: str) -> str:
    """The legacy setting only. Empty when the person never saved a URL."""
    return _text(db, f"{type_name}_base_url").strip().rstrip("/")


def apply_source_base_urls(db: Session, values: dict) -> None:
    """Mirror ``{type}_base_url`` into provider.config. The setting row stays."""
    classes = {cls.type: cls for cls in _download_classes()}
    for key in values:
        if not key.endswith("_base_url"):
            continue
        type_name = key[: -len("_base_url")]
        cls = classes.get(type_name)
        if cls is None or "base_url" not in cls.config_model().model_fields:
            continue
        _write_provider_base_url(db, cls, _text(db, key).strip())


def _download_classes() -> list[type]:
    ranked: list[tuple[int, str, type]] = []
    for cls in _registry().known("source"):
        caps = set(getattr(cls, "capabilities", ()))
        if "links" not in caps and "episode" not in caps and "hive" not in caps:
            continue
        ranked.append((int(getattr(cls, "download_rank", 1000)), cls.type, cls))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [cls for _rank, _name, cls in ranked]


def _write_provider_base_url(db: Session, cls: type, url: str) -> None:
    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == cls.type))
    if provider is None:
        return
    config = dict(provider.config or {})
    fields = set(cls.config_model().model_fields)
    clean = {key: value for key, value in config.items() if key in fields}
    clean["base_url"] = url.strip().rstrip("/")
    try:
        validated = cls.config_model().model_validate(clean)
    except Exception:
        return
    config["base_url"] = validated.base_url
    provider.config = config


_PACER = None
_PACER_DELAY: float | None = None
_CACHE = None
_CACHE_TTL: float | None = None


def _shared_pacer(delay: float):
    global _PACER, _PACER_DELAY
    from mediatracker.providers.sources.throttle import SourcePacer

    if _PACER is None or _PACER_DELAY != delay:
        _PACER = SourcePacer(float(delay))
        _PACER_DELAY = float(delay)
    return _PACER


def _shared_cache(ttl: float):
    global _CACHE, _CACHE_TTL
    from mediatracker.providers.sources.throttle import HtmlCache

    if _CACHE is None or _CACHE_TTL != ttl:
        _CACHE = HtmlCache(float(ttl))
        _CACHE_TTL = float(ttl)
    return _CACHE


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def episode_source(
    name: str,
    settings: Settings,
    *,
    db: Session,
    base_url: str | None = None,
    base_urls: list[str] | None = None,
    addons: list | None = None,
    transport=None,
):
    """Open a download-only source by capability. The catalog never calls this."""
    from mediatracker.core.source_availability import source_usable
    from mediatracker.providers.sources.registry import for_type

    if not source_usable(db, settings, name):
        return None
    entry = for_type(name)
    if entry is None or "episode" not in getattr(entry, "capabilities", frozenset()):
        return None
    delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
    kwargs: dict = {"transport": transport, "request_delay_seconds": delay}
    if addons is not None:
        return entry.open_episode(settings, addons=addons, **kwargs)
    if base_urls is not None:
        return entry.open_episode(settings, base_urls=base_urls, **kwargs)
    return entry.open_episode(settings, base_url=base_url, **kwargs)


def _int_setting(db: Session, key: str, default: int) -> int:
    raw = _text(db, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
