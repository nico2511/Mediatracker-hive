"""Common interface for catalog sources registered in ``registry.py``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mediatracker.config import Settings
    from mediatracker.db.models import SourceBinding


class SourceEntry(Protocol):
    """One remote catalogue the poller may open. The DB session is always injected."""

    name: str
    capabilities: frozenset[str]

    def provider_id(self, db: Session) -> int:
        """Row id for ``source_binding``. Raises when the provider row is missing."""

    def binding_page_url(self, db: Session, settings: Settings, binding: SourceBinding) -> str | None:
        """Absolute page URL from stored path or legacy URL."""

    def open_poll(
        self,
        db: Session,
        settings: Settings,
        *,
        pacer=None,
        cache=None,
        transport: httpx.BaseTransport | None = None,
        redirect_db: Session | None = None,
    ) -> object | None:
        """Live client for follow polling. Returns ``None`` when not configured."""

    def open_cached(
        self,
        db: Session,
        settings: Settings,
        *,
        pacer=None,
        cache=None,
        transport: httpx.BaseTransport | None = None,
    ) -> object | None:
        """Live client for nouveautés. Shares the poll pacer and HTML cache."""

    def probe_binding_page(
        self,
        db: Session,
        settings: Settings,
        page_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> object:
        """One paced GET on a bound page. Site-specific hoster filters stay in the plugin."""

    def open_episode(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> object | None:
        """Live client for episode downloads. ``None`` when not configured or unsupported."""
