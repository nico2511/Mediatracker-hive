"""hive_only : un sondage Hive sans correspondance doit avancer l'horaire (évite le worker à sommeil 0)."""

from __future__ import annotations

import random
from datetime import timedelta, timezone

import pytest
from tests.conftest import csrf, login
from tests.test_hive_t6 import NOW, _enable, _ruche

from mediatracker.core.cleanup_classify import mark_classify_pass
from mediatracker.core.quality_profile import ensure_default_profile
from mediatracker.core.source_poll import next_due
from mediatracker.core.worker_schedule import compute_sleep_seconds, next_deadline
from mediatracker.db.models import Media, MediaItem
from mediatracker.hive.source_poll import poll_follow_respecting_order

pytest_plugins = ["tests.test_hive_t5"]


def _hive_only(client) -> None:
    login(client)
    _enable(client)
    _ruche(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_only": True, "hive_public_address": "https://mediatracker.example.com"}},
        headers=csrf(client),
    )


@pytest.fixture
def hive_only_scheduled_media(client, app):
    _hive_only(client)
    db = app.state.session_factory()
    try:
        ensure_default_profile(db)
        media = Media(
            type="series",
            title="Serie fictive hive",
            year=2099,
            tmdb_id=88001,
            followed=True,
            new_episode_policy="auto",
            next_source_poll_at=NOW,
            last_source_poll_at=None,
        )
        db.add(media)
        db.flush()
        db.add(
            MediaItem(
                media_id=media.id,
                season=1,
                episode=1,
                watched=False,
                state="missing",
                air_date=NOW.date(),
            )
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()
    return media_id


def test_hive_only_empty_match_advances_next_poll(client, app, hive_only_scheduled_media):
    """Reproduction : sans correctif, last/next restent figés et le worker ne dort pas."""
    settings = app.state.settings
    media_id = hive_only_scheduled_media
    db = app.state.session_factory()
    try:
        report = poll_follow_respecting_order(
            db,
            settings,
            now=NOW,
            rng=random.Random(7),
            commit=True,
        )
        row = db.get(Media, media_id)
        assert report is not None
        assert report.result == "nothing"
        assert report.episodes == 0
        assert row.last_source_poll_at is not None
        nxt = row.next_source_poll_at
        assert nxt is not None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        assert nxt > NOW
        mark_classify_pass(NOW)
        after = NOW + timedelta(seconds=1)
        db.expire_all()
        assert next_due(db, settings, after, commit=False) is None
        deadline = next_deadline(db, settings, after)
        assert deadline is not None and deadline > after
        assert compute_sleep_seconds(db, settings, now=after) > 0.0
    finally:
        db.close()


def test_hive_only_nothing_poll_does_not_repick_same_media(client, app, hive_only_scheduled_media):
    settings = app.state.settings
    media_id = hive_only_scheduled_media
    db = app.state.session_factory()
    try:
        first_due = next_due(db, settings, NOW, commit=False)
        assert first_due is not None
        assert first_due.id == media_id
        poll_follow_respecting_order(db, settings, now=NOW, rng=random.Random(3), commit=True)
        soon = NOW + timedelta(seconds=5)
        second_due = next_due(db, settings, soon, commit=False)
        assert second_due is None or second_due.id != media_id
    finally:
        db.close()