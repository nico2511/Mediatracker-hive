"""Register the providers that ship with v2."""

from mediatracker.providers.debrid.alldebrid import AllDebridProvider
from mediatracker.providers.downloaders.aria2 import Aria2Downloader
from mediatracker.providers.registry import ProviderRegistry
from mediatracker.providers.sources.comet import CometProvider
from mediatracker.providers.sources.free_telecharger import FreeTelechargerSource
from mediatracker.providers.sources.hive import HiveProvider
from mediatracker.providers.sources.manual import ManualSource
from mediatracker.providers.sources.stremio_addons import StremioAddonsProvider
from mediatracker.providers.sources.torrentio import TorrentioProvider
from mediatracker.providers.sources.wawacity import WawacitySource


def register_builtins(registry: ProviderRegistry) -> None:
    registry.register(ManualSource)
    registry.register(WawacitySource)
    registry.register(FreeTelechargerSource)
    registry.register(CometProvider)
    registry.register(TorrentioProvider)
    registry.register(StremioAddonsProvider)
    registry.register(HiveProvider)
    registry.register(AllDebridProvider)
    registry.register(Aria2Downloader)
    registry.load_entry_points()
