"""A media with nothing to fetch is not polled on every worker tick."""

import logging
import random
from datetime import datetime, timedelta, timezone

from tests.conftest import csrf, login

from mediatracker.core.source_force import pump_forced_source_check, request_source_check
from mediatracker.db.models import Media, MediaItem
from mediatracker.providers.sources.loading import poll_if_due

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class _Source:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, *args, **kwargs):
        self.calls.append("search")
        return []

    def get_links(self, ref):
        self.calls.append("links")
        return []

    def close(self) -> None:
        return None


def _series(client) -> int:
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Rien à prendre",
            "year": 2024,
            "new_episode_policy": "auto",
            "series_status": "returning",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_nothing_to_fetch_respects_the_interval(client, app, caplog):
    login(client)
    media_id = _series(client)
    source = _Source()
    db = app.state.session_factory()
    try:
        with caplog.at_level(logging.INFO, logger="mediatracker.core.source_poll"):
            first = poll_if_due(
                db,
                app.state.settings,
                now=NOW,
                sources=[(1, source)],
                rng=random.Random(1),
            )
    finally:
        db.close()
    assert first is not None
    assert first.result == "nothing"
    assert first.episodes == 0
    assert source.calls == []

    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.last_poll_result == "nothing"
        nxt = row.next_source_poll_at
        assert nxt is not None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        assert nxt - NOW >= timedelta(hours=12)
        with caplog.at_level(logging.INFO, logger="mediatracker.core.source_poll"):
            second = poll_if_due(
                db,
                app.state.settings,
                now=NOW + timedelta(seconds=5),
                sources=[(1, source)],
                rng=random.Random(1),
            )
    finally:
        db.close()
    assert second is None
    assert source.calls == []
    assert caplog.text.count("Contrôle source") == 1


def test_forced_wave_does_not_repoll_when_the_poll_leaves_the_clock_behind(client, app, monkeypatch):
    login(client)
    media_id = _series(client)
    calls: list[int] = []

    def fake_orchestrate(_factory, _settings, media_id, *, force=False, **_kwargs):
        assert force is True
        calls.append(media_id)
        return None

    monkeypatch.setattr(
        "mediatracker.core.source_force.orchestrate_profile_first_poll",
        fake_orchestrate,
    )
    db = app.state.session_factory()
    try:
        request_source_check(db)
        assert pump_forced_source_check(db, app.state.settings) is True
        assert pump_forced_source_check(db, app.state.settings) is False
    finally:
        db.close()
    assert calls == [media_id]


def test_episode_row_includes_the_filename(client, app):
    login(client)
    media_id = _series(client)
    created = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Pilote"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, created.json()["id"])
        item.file_path = "/data/media/Series/Rien à prendre/Rien.a.prendre.S01E01.mkv"
        item.downloaded = True
        db.commit()
    finally:
        db.close()
    listed = client.get(f"/api/v1/media/{media_id}/items")
    episode = listed.json()["seasons"][0]["episodes"][0]
    assert episode["filename"] == "Rien.a.prendre.S01E01.mkv"
    assert episode["downloaded"] is True
