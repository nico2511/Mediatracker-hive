"""Registry of catalog download sources keyed by provider ``type``."""

import logging

from mediatracker.providers.sources.registry_base import SourceEntry

logger = logging.getLogger(__name__)

_by_name: dict[str, SourceEntry] = {}
_LOADED = False


def _ensure_builtin_sources() -> None:
    global _LOADED
    if _LOADED:
        return
    import mediatracker.providers.sources as _sources  # noqa: F401

    _LOADED = True


def register(entry: SourceEntry) -> None:
    _by_name[entry.name] = entry


def get(name: str) -> SourceEntry:
    _ensure_builtin_sources()
    try:
        return _by_name[name]
    except KeyError:
        raise KeyError(f"unknown catalog source: {name}") from None


def for_type(type_name: str) -> SourceEntry | None:
    """Resolve a provider type. Unknown names are logged and ignored."""
    _ensure_builtin_sources()
    entry = _by_name.get(type_name)
    if entry is not None:
        return entry
    logger.debug("Type de source inconnu %r : ignoré pour le poll.", type_name)
    return None


def all_sources() -> tuple[SourceEntry, ...]:
    _ensure_builtin_sources()
    return tuple(_by_name.values())


def known_types() -> frozenset[str]:
    _ensure_builtin_sources()
    return frozenset(_by_name)
