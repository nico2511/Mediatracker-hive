"""GET /requests/wanted : rattrapage auto-prise recherché."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import func, select
from tests.conftest import login
from tests.test_hive_t2 import _enable_hive
from tests.test_hive_wanted_v3 import NOW, REMOTE, _add_cherche, _film_cle, _patch_tmdb

from mediatracker.db.models import HiveWantedVolunteer, Setting
from mediatracker.hive.signal_policy import HIVE_AUTO_TAKE_WANTED_KEY


def test_get_wanted_auto_takes_when_setting_on(client, settings, monkeypatch):
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(883001))
        db.merge(Setting(key=HIVE_AUTO_TAKE_WANTED_KEY, value="true", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/hive/requests/wanted")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1
    assert body["items"][0]["volunteering"] is True
    db = client.app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(HiveWantedVolunteer)) == 1
    finally:
        db.close()


def test_get_wanted_skips_auto_take_when_setting_off(client, settings):
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(883002))
        db.merge(Setting(key=HIVE_AUTO_TAKE_WANTED_KEY, value="false", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/hive/requests/wanted")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["volunteering"] is False


def test_get_wanted_swallows_auto_take_error(client, settings, monkeypatch):
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(883003))
        db.merge(Setting(key=HIVE_AUTO_TAKE_WANTED_KEY, value="true", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    with patch(
        "mediatracker.hive.signal_wanted.maybe_try_auto_take_wanted",
        side_effect=RuntimeError("simulated failure"),
    ):
        resp = client.get("/api/v1/hive/requests/wanted")
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()
