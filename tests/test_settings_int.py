"""Réglages entiers partagés et rétention corbeille."""

import asyncio

import pytest
from tests.conftest import csrf, login

from mediatracker.db.models import Setting
from mediatracker.settings_int import int_setting, trash_retention_days


def test_int_setting_uses_db_then_default(client, app):
    settings = app.state.settings
    login(client)
    with app.state.session_factory() as db:
        assert int_setting(db, "trash_retention_days", settings.trash_retention_days) == settings.trash_retention_days
        db.merge(Setting(key="trash_retention_days", value="12", is_secret=False))
        db.commit()
        assert int_setting(db, "trash_retention_days", settings.trash_retention_days) == 12
        db.merge(Setting(key="trash_retention_days", value="oops", is_secret=False))
        db.commit()
        assert int_setting(db, "trash_retention_days", settings.trash_retention_days) == settings.trash_retention_days


def test_trash_retention_days_alias(client, app):
    settings = app.state.settings
    login(client)
    with app.state.session_factory() as db:
        expected = int_setting(db, "trash_retention_days", settings.trash_retention_days)
        assert trash_retention_days(db, settings) == expected


class _SizedUpload:
    def __init__(self, size: int, *, filename: str = "watch.csv") -> None:
        self.filename = filename
        self._size = size
        self._read = 0

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = 64 * 1024
        if self._read >= self._size:
            return b""
        take = min(n, self._size - self._read)
        self._read += take
        return b"x" * take


def test_jellyfin_csv_preview_rejects_oversized_without_full_read():
    from mediatracker.api.v1.jellyfin_history import JELLYFIN_HISTORY_CSV_MAX_BYTES, _read_csv

    upload = _SizedUpload(JELLYFIN_HISTORY_CSV_MAX_BYTES + 1)

    async def run() -> None:
        with pytest.raises(Exception) as exc:
            await _read_csv(upload)  # type: ignore[arg-type]
        assert upload._read <= JELLYFIN_HISTORY_CSV_MAX_BYTES + 64 * 1024
        assert getattr(exc.value, "status", None) == 413

    asyncio.run(run())


def test_settings_lists_client_constants(client):
    login(client)
    response = client.get("/api/v1/settings", headers=csrf(client))
    assert response.status_code == 200
    body = response.json()
    assert body["client_constants"] == {
        "hive_join_wait_hours": 48,
        "hive_media_sharing_summary_id_limit": 80,
    }