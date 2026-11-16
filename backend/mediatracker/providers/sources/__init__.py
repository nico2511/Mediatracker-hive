"""Catalog source registry. Remote scrapers register here for polling."""

from mediatracker.providers.sources.comet_plugin import CometSourceEntry
from mediatracker.providers.sources.free_telecharger_plugin import FreeTelechargerSourceEntry
from mediatracker.providers.sources.hive_plugin import HiveSourceEntry
from mediatracker.providers.sources.manual_plugin import ManualSourceEntry
from mediatracker.providers.sources.registry import all_sources, for_type, get, known_types, register
from mediatracker.providers.sources.stremio_addons_plugin import StremioAddonsSourceEntry
from mediatracker.providers.sources.torrentio_plugin import TorrentioSourceEntry
from mediatracker.providers.sources.wawacity_plugin import WawacitySourceEntry

register(WawacitySourceEntry())
register(FreeTelechargerSourceEntry())
register(CometSourceEntry())
register(TorrentioSourceEntry())
register(StremioAddonsSourceEntry())
register(HiveSourceEntry())
register(ManualSourceEntry())

__all__ = ["all_sources", "for_type", "get", "known_types", "register"]
