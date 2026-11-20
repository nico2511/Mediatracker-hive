"""Constantes serveur exposées au client (durées et limites UI)."""

from __future__ import annotations

from dataclasses import dataclass

HIVE_JOIN_WAIT_HOURS = 48
HIVE_MEDIA_SHARING_SUMMARY_ID_LIMIT = 80


@dataclass(frozen=True, slots=True)
class ClientConstants:
    hive_join_wait_hours: int
    hive_media_sharing_summary_id_limit: int


def client_constants() -> ClientConstants:
    return ClientConstants(
        hive_join_wait_hours=HIVE_JOIN_WAIT_HOURS,
        hive_media_sharing_summary_id_limit=HIVE_MEDIA_SHARING_SUMMARY_ID_LIMIT,
    )
