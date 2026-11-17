"""Public default URLs for Comet and Torrentio (single source for API and config fallbacks)."""

from __future__ import annotations

from pydantic import BaseModel

from mediatracker.core.public_source_urls import DEFAULT_COMET_INSTANCE_URLS, DEFAULT_TORRENTIO_BASE_URL


class SourceUrlDefaults(BaseModel):
    comet_base_urls: list[str]
    torrentio_base_url: str


def source_url_defaults() -> SourceUrlDefaults:
    return SourceUrlDefaults(
        comet_base_urls=list(DEFAULT_COMET_INSTANCE_URLS),
        torrentio_base_url=DEFAULT_TORRENTIO_BASE_URL,
    )