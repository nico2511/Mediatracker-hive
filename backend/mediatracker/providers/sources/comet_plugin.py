"""Comet download source registered for episode fallback (after Wawacity hosters)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from mediatracker.core.source_binding import SourceBindingProbe
from mediatracker.providers.sources.comet import CometProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mediatracker.config import Settings
    from mediatracker.db.models import SourceBinding


class CometSourceEntry:
    name = "comet"
    capabilities = frozenset({"episode"})
    download_rank = 20

    def provider_id(self, db: Session) -> int:
        from sqlalchemy import select

        from mediatracker.db.models import Provider

        row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == self.name))
        if row is None:
            raise ValueError("La source Comet n'est pas disponible.")
        return row.id

    def binding_page_url(self, db: Session, settings: Settings, binding: SourceBinding) -> str | None:
        del db, settings, binding
        return None

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
        del db, settings, pacer, cache, transport, redirect_db
        return None

    def open_cached(
        self,
        db: Session,
        settings: Settings,
        *,
        pacer=None,
        cache=None,
        transport: httpx.BaseTransport | None = None,
    ) -> object | None:
        del db, settings, pacer, cache, transport
        return None

    def probe_binding_page(
        self,
        db: Session,
        settings: Settings,
        page_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> SourceBindingProbe:
        del db, settings, page_url, transport
        return SourceBindingProbe(False, None, {}, message="Comet ne lie pas de pages catalogue.")

    def open_episode(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        base_urls: list[str] | None = None,
        transport: httpx.BaseTransport | None = None,
        request_delay_seconds: float | None = None,
    ) -> object | None:
        return CometProvider.open_episode(
            settings,
            base_url=base_url,
            base_urls=base_urls,
            transport=transport,
            request_delay_seconds=request_delay_seconds,
        )
