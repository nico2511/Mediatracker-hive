"""Fenêtre horaire Hive : fuseau local, passage minuit, mint hors plage."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from mediatracker.hive.transfer_settings import in_transfer_window

pytest_plugins = ["tests.test_hive_t5"]


def _db_with_window(start: int, end: int) -> MagicMock:
    values = {
        "hive_transfer_window_start": str(start),
        "hive_transfer_window_end": str(end),
    }

    def _get(_session, key: str):
        if key not in values:
            return None
        row = MagicMock()
        row.value = values[key]
        return row

    db = MagicMock()
    db.get.side_effect = _get
    return db


@pytest.mark.parametrize(
    ("start", "end", "local_hour", "local_minute", "expected"),
    [
        (22 * 60, 6 * 60, 23, 0, True),
        (22 * 60, 6 * 60, 2, 30, True),
        (22 * 60, 6 * 60, 12, 0, False),
        (0, 24 * 60, 15, 0, True),
        (10 * 60, 10 * 60, 8, 0, True),
    ],
)
def test_in_transfer_window_midnight_wrap(monkeypatch, start, end, local_hour, local_minute, expected):
    monkeypatch.setenv("TZ", "Europe/Paris")
    db = _db_with_window(start, end)
    fixed = datetime(2026, 1, 15, local_hour, local_minute, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "mediatracker.hive.transfer_settings._minutes_since_midnight_local",
        lambda _now: local_hour * 60 + local_minute,
    )
    assert in_transfer_window(db, fixed) is expected


def test_in_transfer_window_uses_paris_offset_winter(monkeypatch):
    """03:30 UTC en hiver = 04:30 Paris ; fenêtre 04:00–05:00 locale."""
    monkeypatch.setenv("TZ", "Europe/Paris")
    db = _db_with_window(4 * 60, 5 * 60)
    open_at = datetime(2026, 1, 15, 3, 45, tzinfo=timezone.utc)
    closed_at = datetime(2026, 1, 15, 4, 5, tzinfo=timezone.utc)
    assert in_transfer_window(db, open_at) is True
    assert in_transfer_window(db, closed_at) is False


def test_in_transfer_window_summer_offset(monkeypatch):
    """02:00 UTC en été = 04:00 Paris ; fenêtre 03:00–05:00 locale."""
    monkeypatch.setenv("TZ", "Europe/Paris")
    db = _db_with_window(3 * 60, 5 * 60)
    in_window = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    out_window = datetime(2026, 7, 15, 0, 30, tzinfo=timezone.utc)
    assert in_transfer_window(db, in_window) is True
    assert in_transfer_window(db, out_window) is False


def test_mint_denied_when_window_closed(transfer_pair, monkeypatch):
    from starlette.testclient import TestClient
    from tests.conftest import catalog_min_logical_video_bytes
    from tests.test_hive_fetch_grant_lot1 import _mint_path, _setup_pair
    from tests.test_hive_t5 import NOW, URL_A, URL_B, _seed_file_on_a

    from mediatracker.db.models import Setting
    from mediatracker.hive.identity import load_private_key
    from mediatracker.hive.peer_auth import sign_request_headers
    from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests

    app_a, app_b = transfer_pair
    closed = NOW.replace(hour=3, minute=0)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        monkeypatch.setattr("mediatracker.api.hive_router._now", lambda: closed)
        monkeypatch.setenv("TZ", "Europe/Paris")
        min_video = catalog_min_logical_video_bytes()
        blob = b"W" * min_video
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        db_a = app_a.state.session_factory()
        try:
            db_a.merge(Setting(key="hive_transfer_window_start", value="0"))
            db_a.merge(Setting(key="hive_transfer_window_end", value="1"))
            db_a.commit()
        finally:
            db_a.close()
        path = _mint_path(rid, fid)
        pk = load_private_key(app_b.state.settings)
        headers = sign_request_headers(
            private_key=pk,
            method="POST",
            path=path,
            query="",
            ruche_id=rid,
            body=b"",
            when=closed,
        )
        set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
        assert ca.post(path, headers=headers).status_code == 429