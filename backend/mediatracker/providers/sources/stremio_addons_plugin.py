"""Generic Stremio addons download source."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from mediatracker.core.source_binding import SourceBindingProbe
from mediatracker.providers.sources.stremio_addons import StremioAddonsProvider
from mediatracker.providers.sources.stremio_addons_config import StremioAddonEntry

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mediatracker.config import Settings
    from mediatracker.db.models import SourceBinding


class StremioAddonsSourceEntry:
    name = "stremio_addons"
    capabilities = frozenset({"episode"})
    download_rank = 23

    def provider_id(self, db: Session) -> int:
        from sqlalchemy import select

        from mediatracker.db.models import Provider

        row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == self.name))
        if row is None:
            raise ValueError("La source Addons Stremio n'est pas disponible.")
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
        return SourceBindingProbe(False, None, {}, message="Les addons Stremio ne lient pas de pages catalogue.")

    def open_episode(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        base_urls: list[str] | None = None,
        addons: list[StremioAddonEntry] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> object | None:
        del base_urls
        if addons is not None:
            return StremioAddonsProvider.open_episode(settings, addons=addons, transport=transport)
        if base_url:
            return StremioAddonsProvider.open_episode(
                settings,
                addons=[StremioAddonEntry(url=base_url, enabled=True)],
                transport=transport,
            )
        return None