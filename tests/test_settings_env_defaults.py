"""Environment defaults align with selection constants."""

from mediatracker.config import get_settings
from mediatracker.core.selection import DEFAULT_DOWNLOAD_SOURCE_ORDER, DEFAULT_HOSTER_PREFERENCE


def test_settings_default_source_order_matches_selection():
    settings = get_settings()
    assert settings.download_source_order == DEFAULT_DOWNLOAD_SOURCE_ORDER
    assert settings.hoster_preference == ",".join(DEFAULT_HOSTER_PREFERENCE)