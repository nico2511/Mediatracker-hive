"""Entrée de registre pour la source Hive (pas de poll HTTP)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from mediatracker.core.source_binding import SourceBindingProbe

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mediatracker.config import Settings
    from mediatracker.db.models import SourceBinding


class HiveSourceEntry:
    name = "hive"
    capabilities = frozenset({"hive"})
    download_rank = 15

    def provider_id(self, db: Session) -> int:
        from sqlalchemy import select

        from mediatracker.db.models import Provider

        row = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == self.name))
        if row is None:
            raise ValueError("La source Hive n'est pas disponible.")
        return row.id

    def binding_page_url(self, db: Session, settings: Settings, binding: SourceBinding) -> str | None:
        del db, settings, binding
        return None

    def open_poll(self, db: Session, settings: Settings, **kwargs) -> object | None:
        del db, settings, kwargs
        return None

    def open_cached(self, db: Session, settings: Settings, **kwargs) -> object | None:
        del db, settings, kwargs
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
        return SourceBindingProbe(False, None, {}, message="Hive ne sonde pas de page distante.")

    def open_episode(self, settings, *, base_url: str | None = None, transport=None) -> object | None:
        del settings, base_url, transport
        return None
