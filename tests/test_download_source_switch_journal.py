"""Journal : bascules de source sans spam (noms fictifs)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from mediatracker.core.pipeline.jobs import _record_download_source_switch
from mediatracker.db.models import Event

BASE = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session(client, app):
    db = app.state.session_factory()
    yield db
    db.close()


def test_source_switch_journal_once_per_hour_and_pair(db_session):
    row = SimpleNamespace(id=5, state="pending")
    item = SimpleNamespace(id=None, season=1, episode=2)
    media = SimpleNamespace(id=None, title="Ouvrage Démo")

    _record_download_source_switch(
        db_session, row, media, item, "wawacity", "comet", now=BASE
    )
    _record_download_source_switch(
        db_session,
        row,
        media,
        item,
        "wawacity",
        "comet",
        now=BASE + timedelta(minutes=3),
    )
    db_session.commit()

    count = db_session.scalar(
        select(func.count()).select_from(Event).where(Event.type == "download.source_switched")
    )
    assert count == 1
    event = db_session.scalar(select(Event).where(Event.type == "download.source_switched"))
    assert event is not None
    assert event.payload["message"] == "Bascule de source : Wawacity vers Comet"
    assert event.payload["from_label"] == "Wawacity"
    assert event.payload["to_label"] == "Comet"


def test_source_switch_journal_allows_new_row_after_state_change(db_session):
    row = SimpleNamespace(id=8, state="pending")
    item = SimpleNamespace(id=None, season=1, episode=1)
    media = SimpleNamespace(id=None, title="Série Démo")

    _record_download_source_switch(db_session, row, media, item, "comet", "torrentio", now=BASE)
    row.state = "queued"
    _record_download_source_switch(
        db_session, row, media, item, "comet", "torrentio", now=BASE + timedelta(minutes=1)
    )
    db_session.commit()

    rows = db_session.scalars(select(Event).where(Event.type == "download.source_switched")).all()
    assert len(rows) == 2


def test_source_switch_journal_allows_new_row_after_cooldown(db_session):
    row = SimpleNamespace(id=9, state="pending")
    item = SimpleNamespace(id=None, season=2, episode=3)
    media = SimpleNamespace(id=None, title="Alpha Démo")

    _record_download_source_switch(
        db_session, row, media, item, "free_telecharger", "hive", now=BASE
    )
    _record_download_source_switch(
        db_session,
        row,
        media,
        item,
        "free_telecharger",
        "hive",
        now=BASE + timedelta(hours=1, minutes=5),
    )
    db_session.commit()

    count = db_session.scalar(
        select(func.count()).select_from(Event).where(Event.type == "download.source_switched")
    )
    assert count == 2


def test_download_pass_does_not_journal_switch_through_globally_disabled_source(client, app, monkeypatch):
    """Une source absente du réglage global n’est ni appelée ni citée dans une bascule."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlalchemy import select
    from tests.conftest import FICTIVE_ALLDEBRID_API_KEY, csrf, login

    monkeypatch.setenv("ALLDEBRID_API_KEY", FICTIVE_ALLDEBRID_API_KEY)
    from mediatracker.config import get_settings

    app.state.settings = get_settings()
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity,comet"}},
        headers=csrf(client),
    )

    from mediatracker.core.pipeline.jobs import _record_download_source_switch, _usable_download_sources
    from mediatracker.db.models import Event, Media

    db = app.state.session_factory()
    calls: list[str] = []
    try:

        def fake_from_named_source(_db, name, *_args, **_kwargs):
            calls.append(name)
            return None

        media = Media(
            type="series",
            title="Bascule filtrée",
            year=2022,
            followed=True,
            source_order="wawacity,free_telecharger,comet",
        )
        row = SimpleNamespace(id=12, state="pending")
        item = SimpleNamespace(id=None, season=1, episode=1)
        now = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
        order = _usable_download_sources(db, media, app.state.settings)
        assert order == ["wawacity", "comet"]
        for index, name in enumerate(order):
            outcome = fake_from_named_source(db, name, row, item, media, now, [], None, None, app.state.settings, [])
            if outcome is None and index < len(order) - 1:
                _record_download_source_switch(db, row, media, item, order[index], order[index + 1], now=now)
        db.commit()
        assert calls == ["wawacity", "comet"]
        assert "free_telecharger" not in calls
        messages = [
            row.payload.get("message", "")
            for row in db.scalars(select(Event).where(Event.type == "download.source_switched")).all()
        ]
        assert messages == ["Bascule de source : Wawacity vers Comet"]
        assert all("Free" not in msg and "free_telecharger" not in msg for msg in messages)
    finally:
        db.close()
