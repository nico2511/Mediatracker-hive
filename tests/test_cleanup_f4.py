"""F4: préavis de corbeille, raisons cleanup.done, notifications HA."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.cleanup import Plan, _aware, _notify_upcoming, maintenance, notify_cleanup_done
from mediatracker.db.models import Event, Media, MediaItem, TrashEntry
from mediatracker.events.homeassistant import format_homeassistant
from mediatracker.events.text import notification_text


def _enable(client):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "auto_delete_enabled": True,
                "auto_delete_days": 14,
                "cleanup_notice_hours": 24,
                "trash_retention_days": 7,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _media(db, **kwargs):
    row = Media(type=kwargs.pop("type", "film"), title=kwargs.pop("title", "Film"), **kwargs)
    db.add(row)
    db.flush()
    return row


def _file(root: Path, name: str, payload: bytes = b"video") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _item(db, media, path: Path, *, watched_at=None):
    row = MediaItem(
        media_id=media.id,
        title=media.title,
        state="downloaded",
        downloaded=True,
        downloaded_at=watched_at,
        watched=True,
        watched_at=watched_at,
        file_path=str(path),
    )
    db.add(row)
    db.flush()
    return row


def test_notice_rearms_after_file_reappears(client, app):
    """Fictif « Brume » : un ancien préavis ne déclenche pas la corbeille après réapparition."""
    _enable(client)
    base = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        path = _file(movies, "Brume/Brume.mkv", b"x" * 40)
        media = _media(db, title="Brume", auto_delete=True)
        item = _item(db, media, path, watched_at=base - timedelta(days=30))
        db.commit()

        maintenance(db, app.state.settings, now=base)
        assert path.is_file()
        maintenance(db, app.state.settings, now=base + timedelta(hours=25))
        assert not path.is_file()

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 40)
        back = base + timedelta(hours=30)
        item.downloaded = True
        item.file_path = str(path)
        item.downloaded_at = back
        db.commit()

        maintenance(db, app.state.settings, now=back + timedelta(hours=1))
        assert path.is_file()
        dedupe = f"cleanup.upcoming:media:{media.id}"
        notice = db.scalar(select(Event).where(Event.type == "cleanup.upcoming", Event.dedupe_key == dedupe))
        assert notice is not None
        assert _aware(notice.ts) >= back
        maintenance(db, app.state.settings, now=back + timedelta(hours=24))
        assert path.is_file()
        maintenance(db, app.state.settings, now=back + timedelta(hours=25))
        assert not path.is_file()
    finally:
        db.close()


def test_cleanup_done_reasons_and_ha_text(client, app):
    _enable(client)
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        media = _media(db, title="Cendre")
        db.commit()
        for index, (reason, title, message_needle) in enumerate(
            (
                ("manual", "Nettoyage manuel", "manuel"),
                ("auto", "Nettoyage automatique", "automatique"),
                ("duplicate", "Doublon retiré", "doublon"),
            )
        ):
            tick = now + timedelta(seconds=index)
            entry = TrashEntry(
                original_path=f"/data/{reason}.mkv",
                trash_path=f"/data/.trash/{reason}.mkv",
                size=10,
                media_id=media.id,
                label="Cendre",
                reason=reason,
                deleted_at=tick,
                purge_after=tick + timedelta(days=7),
            )
            notify_cleanup_done(db, media, "media", None, [entry], tick, reason=reason)
            db.flush()
            event = db.scalars(
                select(Event).where(Event.type == "cleanup.done").order_by(Event.id.desc()).limit(1)
            ).one()
            assert event.payload["reason"] == reason
            assert event.payload["count"] == 1
            title_ha, message_ha = notification_text("cleanup.done", event.payload)
            body = format_homeassistant("cleanup.done", event.payload)
            assert body["title"] == title
            assert message_needle in message_ha.casefold()
            assert body["message"] == message_ha
        db.commit()
    finally:
        db.close()


def test_cleanup_events_never_fire_with_zero_files(client, app):
    _enable(client)
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        media = _media(db, title="Vide")
        plan = Plan(
            key=f"media:{media.id}",
            media=media,
            season=None,
            files=[],
            eligible_at=now,
            label="Vide",
        )
        before = db.scalar(select(Event).where(Event.type.in_(("cleanup.upcoming", "cleanup.done"))))
        _notify_upcoming(db, app.state.settings, plan, now)
        notify_cleanup_done(db, media, "media", None, [], now, reason="manual")
        db.commit()
        after = list(db.scalars(select(Event).where(Event.type.in_(("cleanup.upcoming", "cleanup.done")))).all())
        assert before is None and after == []
    finally:
        db.close()
