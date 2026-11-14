"""Cooldown between Wawacity searches on the same fiche."""

from datetime import datetime, timedelta, timezone

from tests.conftest import csrf, login

from mediatracker.core.remote_search_limit import (
    REMOTE_SEARCH_COOLDOWN,
    begin_remote_search,
    remote_search_allowed,
    remote_search_cooldown,
    remote_search_cooldown_hours,
    remote_search_limit_phrase,
)
from mediatracker.db.models import Media, Setting

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _media(*, last: datetime | None) -> Media:
    return Media(type="film", title="Atelier Brume", year=2026, last_remote_search_at=last)


def test_default_cooldown_is_six_hours(client, app):
    del client
    db = app.state.session_factory()
    try:
        settings = app.state.settings
        assert remote_search_cooldown_hours(db, settings) == 6
        assert remote_search_cooldown(db, settings) == REMOTE_SEARCH_COOLDOWN
        assert remote_search_limit_phrase(db, settings) == "limite 6 h"
    finally:
        db.close()


def test_configured_cooldown_two_hours(client, app):
    del client
    db = app.state.session_factory()
    try:
        settings = app.state.settings
        row = db.get(Setting, "remote_search_cooldown_hours")
        if row is None:
            db.add(Setting(key="remote_search_cooldown_hours", value="2", is_secret=False))
        else:
            row.value = "2"
        db.commit()
        cooldown = remote_search_cooldown(db, settings)
        last = NOW - timedelta(hours=1, minutes=59)
        media = _media(last=last)
        assert remote_search_allowed(media, NOW, cooldown=cooldown) is False
        assert begin_remote_search(media, NOW, db, settings) is False

        media.last_remote_search_at = NOW - timedelta(hours=2)
        assert remote_search_allowed(media, NOW, cooldown=cooldown) is True
        assert begin_remote_search(media, NOW, db, settings) is True
    finally:
        db.close()


def test_remote_search_allowed_after_six_hours_default():
    cooldown = REMOTE_SEARCH_COOLDOWN
    last = NOW - timedelta(hours=6)
    media = _media(last=last)
    assert remote_search_allowed(media, NOW, cooldown=cooldown) is True


def test_remote_search_refused_at_five_hours_fifty_nine_default():
    cooldown = REMOTE_SEARCH_COOLDOWN
    last = NOW - timedelta(hours=5, minutes=59)
    media = _media(last=last)
    assert remote_search_allowed(media, NOW, cooldown=cooldown) is False


def test_settings_bounds(client, app):
    login(client)
    low = client.patch(
        "/api/v1/settings",
        json={"values": {"remote_search_cooldown_hours": 0}},
        headers=csrf(client),
    )
    assert low.status_code == 422
    high = client.patch(
        "/api/v1/settings",
        json={"values": {"remote_search_cooldown_hours": 73}},
        headers=csrf(client),
    )
    assert high.status_code == 422
    ok = client.patch(
        "/api/v1/settings",
        json={"values": {"remote_search_cooldown_hours": 1}},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text
    top = client.patch(
        "/api/v1/settings",
        json={"values": {"remote_search_cooldown_hours": 72}},
        headers=csrf(client),
    )
    assert top.status_code == 200, top.text
