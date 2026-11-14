"""Correctif 2 — journal regroupement des vérifications et backoff inchangé."""

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from tests.conftest import csrf, login

from mediatracker.core.source_poll import run_poll
from mediatracker.db.models import Event, Media
from mediatracker.events import record_event

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
MESSAGE = "Aucun épisode attendu pour le moment."


class _Source:
    def search(self, *args, **kwargs):
        return []

    def get_links(self, ref):
        del ref
        return []

    def close(self) -> None:
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _series(client):
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Titre factice",
            "year": 2024,
            "new_episode_policy": "auto",
            "series_status": "returning",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _poll_nothing(app, media_id: int, *, moment: datetime, force: bool = False):
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        source = _Source()
        report = run_poll(
            db,
            media,
            1,
            source,
            app.state.settings,
            now=moment,
            rng=random.Random(3),
            force=force,
        )
        db.commit()
        return report
    finally:
        db.close()


def test_three_identical_checks_fold_to_one_journal_row(client, app):
    login(client)
    media_id = _series(client)
    for offset in range(3):
        _poll_nothing(app, media_id, moment=NOW + timedelta(hours=offset), force=True)

    db = app.state.session_factory()
    try:
        rows = list(db.scalars(select(Event).where(Event.type == "source.checked", Event.media_id == media_id)))
        assert len(rows) == 1
        row = rows[0]
        assert row.repeat_count == 3
        assert _aware(row.last_seen_at) == NOW + timedelta(hours=2)
        assert _aware(row.ts) == NOW + timedelta(hours=2)
    finally:
        db.close()

    listed = client.get("/api/v1/events", params={"type": "source.checked", "media_id": media_id})
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["repeat_count"] == 3
    assert item["last_seen_at"].startswith("2026-10-11T14:")


def test_intercalated_event_breaks_fold_and_keeps_chronology(client, app):
    login(client)
    media_id = _series(client)
    payload = {"result": "nothing", "message": MESSAGE}
    db = app.state.session_factory()
    try:
        record_event(db, "source.checked", media_id=media_id, payload=payload, when=NOW)
        record_event(
            db,
            "download.queued",
            media_id=media_id,
            payload={"title": "Titre factice", "message": "Mise en file factice."},
            when=NOW + timedelta(minutes=1),
        )
        record_event(
            db,
            "source.checked",
            media_id=media_id,
            payload=payload,
            when=NOW + timedelta(minutes=2),
        )
        db.commit()
        rows = list(
            db.scalars(
                select(Event)
                .where(
                    Event.media_id == media_id,
                    Event.type.in_(("source.checked", "download.queued")),
                )
                .order_by(Event.ts.asc(), Event.id.asc())
            ).all()
        )
        assert len(rows) == 3
        assert [row.type for row in rows] == ["source.checked", "download.queued", "source.checked"]
        assert rows[0].repeat_count == 1
        assert rows[2].repeat_count == 1
        assert _aware(rows[0].ts) == NOW
        assert _aware(rows[1].ts) == NOW + timedelta(minutes=1)
        assert _aware(rows[2].ts) == NOW + timedelta(minutes=2)
    finally:
        db.close()


def test_different_message_starts_new_journal_row(client, app):
    login(client)
    media_id = _series(client)
    db = app.state.session_factory()
    try:
        record_event(
            db,
            "source.checked",
            media_id=media_id,
            payload={"result": "nothing", "message": MESSAGE},
            when=NOW,
        )
        record_event(
            db,
            "source.checked",
            media_id=media_id,
            payload={"result": "nothing", "message": "Autre message factice."},
            when=NOW + timedelta(minutes=1),
        )
        record_event(
            db,
            "source.checked",
            media_id=media_id,
            payload={"result": "nothing", "message": MESSAGE},
            when=NOW + timedelta(minutes=2),
        )
        db.commit()
        count = db.scalar(
            select(func.count()).select_from(Event).where(
                Event.type == "source.checked",
                Event.media_id == media_id,
            )
        )
        assert count == 3
        last = db.scalar(
            select(Event)
            .where(Event.type == "source.checked", Event.media_id == media_id)
            .order_by(Event.id.desc())
        )
        assert last is not None
        assert last.repeat_count == 1
    finally:
        db.close()


def test_unchanged_poll_backoff_doubles_then_resets(client, app):
    login(client)
    media_id = _series(client)
    _poll_nothing(app, media_id, moment=NOW)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        first_delta = _aware(row.next_source_poll_at) - NOW
        assert first_delta >= timedelta(hours=12)
    finally:
        db.close()

    later = NOW + timedelta(hours=13)
    _poll_nothing(app, media_id, moment=later)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.poll_unchanged_streak == 1
        second_delta = _aware(row.next_source_poll_at) - later
        assert second_delta >= first_delta * 1.8
    finally:
        db.close()

    changed = NOW + timedelta(hours=26)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        row.last_poll_message = "Message intercalé factice."
        row.next_source_poll_at = changed - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    _poll_nothing(app, media_id, moment=changed)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.poll_unchanged_streak == 0
        reset_delta = _aware(row.next_source_poll_at) - changed
        assert reset_delta < first_delta * 1.5
    finally:
        db.close()

    forced = NOW + timedelta(hours=40)
    _poll_nothing(app, media_id, moment=forced, force=True)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.poll_unchanged_streak == 0
    finally:
        db.close()
