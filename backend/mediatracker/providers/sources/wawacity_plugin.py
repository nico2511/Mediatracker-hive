"""Wawacity catalog source registered for polling and page bindings."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import httpx

from mediatracker.core.source_binding import SourceBindingProbe, url_from_path
from mediatracker.providers.base import SourceFetchError, SourceRef
from mediatracker.providers.sources.wawacity import _KEEP_HOSTERS, WawacitySource

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mediatracker.config import Settings
    from mediatracker.db.models import SourceBinding


class WawacitySourceEntry:
    name = "wawacity"
    capabilities = WawacitySource.capabilities
    download_rank = WawacitySource.download_rank

    def provider_id(self, db: Session) -> int:
        from sqlalchemy import select

        from mediatracker.db.models import Provider

        row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == self.name))
        if row is None:
            raise ValueError("La source Wawacity n'est pas disponible.")
        return row.id

    def binding_page_url(self, db: Session, settings: Settings, binding: SourceBinding) -> str | None:
        from mediatracker.providers.sources.loading import source_base_url

        base = source_base_url(db, settings, self.name)
        resolved = url_from_path(base, binding.path)
        if resolved:
            return resolved
        return (binding.url or "").strip() or None

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
        return WawacitySource.open_poll(
            db,
            settings,
            pacer=pacer,
            cache=cache,
            transport=transport,
            redirect_db=redirect_db,
        )

    def open_cached(
        self,
        db: Session,
        settings: Settings,
        *,
        pacer=None,
        cache=None,
        transport: httpx.BaseTransport | None = None,
    ) -> object | None:
        return WawacitySource.open_cached(
            db,
            settings,
            pacer=pacer,
            cache=cache,
            transport=transport,
        )

    def probe_binding_page(
        self,
        db: Session,
        settings: Settings,
        page_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> SourceBindingProbe:
        from mediatracker.providers.sources.loading import _int_setting, _shared_cache, _shared_pacer, wawacity_config

        delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
        ttl = _int_setting(db, "source_cache_seconds", settings.source_cache_seconds)
        config = wawacity_config(db, settings)
        if not config.base_url:
            return SourceBindingProbe(False, None, {}, message="L'URL Wawacity n'est pas configurée.")
        source = WawacitySource(
            config,
            transport=transport,
            pacer=_shared_pacer(float(delay)),
            cache=_shared_cache(float(ttl)),
        )
        try:
            ref = SourceRef(ref=page_url, url=page_url)
            heading, links, _alternates = source._page(ref)
            allowed = set(_KEEP_HOSTERS)
            counts: Counter[str] = Counter()
            for link in links:
                hoster = link.hoster
                if hoster and hoster in allowed:
                    counts[hoster] += 1
            return SourceBindingProbe(
                accessible=True,
                title=heading.title or None,
                links_by_hoster=dict(sorted(counts.items())),
            )
        except Exception as exc:
            message = exc.message if isinstance(exc, SourceFetchError) else str(exc)
            return SourceBindingProbe(False, None, {}, message=message)
        finally:
            source.close()

    def open_episode(
        self,
        settings,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> object | None:
        del settings, base_url, transport
        return None
