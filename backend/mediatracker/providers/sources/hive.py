"""Source Hive : catalogue fusionné local, sans URL distante (T6)."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from mediatracker.providers.base import Health


class HiveConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HiveProvider:
    kind = "source"
    type = "hive"
    capabilities: frozenset[str] = frozenset({"hive"})
    download_rank = 15

    def __init__(self, config: HiveConfig | None = None) -> None:
        self.config = config or HiveConfig()

    @classmethod
    def config_model(cls) -> type[BaseModel]:
        return HiveConfig

    def health(self) -> Health:
        return Health(
            status="ok",
            code=None,
            message="Catalogue Hive fusionné (toutes ruches).",
            checked_at=datetime.now(timezone.utc),
        )
