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
