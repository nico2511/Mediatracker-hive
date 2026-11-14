"""Journal download.source_attempt : dédoublonnage par source et mise à jour du motif."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from mediatracker.core.pipeline.source_attempt_journal import record_download_source_attempt
from mediatracker.db.models import Event

BASE = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session(client, app):
    db = app.state.session_factory()
    yield db
    db.close()


def _stub_row():
    from types import SimpleNamespace

    row = SimpleNamespace(id=42)
    media = SimpleNamespace(id=None, title="Ouvrage Démo")
    item = SimpleNamespace(id=None, season=1, episode=3)
    return row, media, item


def test_source_attempt_journal_stable_when_issue_unchanged(db_session):
    row, media, item = _stub_row()
    record_download_source_attempt(
        db_session, row, media, item, "comet", issue="empty", now=BASE
    )
    record_download_source_attempt(
        db_session, row, media, item, "comet", issue="empty", now=BASE + timedelta(minutes=2)
    )
    db_session.commit()
    count = db_session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.type == "download.source_attempt", Event.dedupe_key == "download.source_attempt:42:comet")
    )
    assert count == 1
    event = db_session.scalar(
        select(Event).where(
            Event.type == "download.source_attempt",
            Event.dedupe_key == "download.source_attempt:42:comet",
        )
    )
    assert event is not None
    assert event.payload["message"] == "Comet : aucun résultat."


def test_source_attempt_journal_updates_when_issue_changes(db_session):
    row, media, item = _stub_row()
    record_download_source_attempt(db_session, row, media, item, "wawacity", issue="empty", now=BASE)
    record_download_source_attempt(
        db_session,
        row,
        media,
        item,
        "wawacity",
        issue="found",
        link_count=2,
        now=BASE + timedelta(seconds=30),
    )
    db_session.commit()
    count = db_session.scalar(
        select(func.count()).select_from(Event).where(Event.type == "download.source_attempt")
    )
    assert count == 1
    event = db_session.scalar(select(Event).where(Event.type == "download.source_attempt"))
    assert event is not None
    assert event.payload["issue"] == "found"
    assert event.payload["link_count"] == 2
