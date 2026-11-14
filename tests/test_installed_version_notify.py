"""Notification when Watchtower installs a new image (X1)."""

from datetime import datetime, timezone

from tests.conftest import csrf, login

from mediatracker.core.installed_version import FINGERPRINT_KEY, observe_installed_version
from mediatracker.db.models import Event, Setting, WebhookDelivery
from mediatracker.db.session import session_scope


def _seed_fingerprint(db, value: str) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(Setting, FINGERPRINT_KEY)
    if row is None:
        db.add(Setting(key=FINGERPRINT_KEY, value=value, is_secret=False, updated_at=now))
    else:
        row.value = value
    db.commit()


def _set_notify_on_update(db, enabled: bool) -> None:
    now = datetime.now(timezone.utc)
    row = db.get(Setting, "notify_on_update")
    text = "true" if enabled else "false"
    if row is None:
        db.add(Setting(key="notify_on_update", value=text, is_secret=False, updated_at=now))
    else:
        row.value = text
    db.commit()


def test_notify_on_update_default_false(client, app):
    login(client)
    body = client.get("/api/v1/settings").json()
    row = next(item for item in body["settings"] if item["key"] == "notify_on_update")
    assert row["value"] is False


def test_first_startup_records_fingerprint_without_event(client, app, monkeypatch):
    login(client)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.1")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aabbccddeeff00112233445566778899aabbccdd")
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, FINGERPRINT_KEY)
        if row is not None:
            db.delete(row)
            db.commit()
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        stored = db.get(Setting, FINGERPRINT_KEY)
        assert stored is not None
        assert stored.value == "2.0.1@aabbccd"
        assert db.query(Event).filter_by(type="system.updated").count() == 0


def test_version_change_with_notify_enabled_schedules_delivery(client, app, monkeypatch):
    login(client)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.0")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "1111111111111111111111111111111111111111")
    with session_scope(app.state.session_factory) as db:
        _seed_fingerprint(db, "2.0.0@1111111")
        _set_notify_on_update(db, True)
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "ntfy",
            "url": "https://ntfy.example/watch",
            "type": "ntfy",
            "events": ["library.imported"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.1")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "2222222222222222222222222222222222222222")
    with session_scope(app.state.session_factory) as db:
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        event = db.query(Event).filter_by(type="system.updated").one()
        assert event.payload["previous_version"] == "2.0.0"
        assert event.payload["new_version"] == "2.0.1"
        deliveries = db.query(WebhookDelivery).filter_by(event_id=event.id).all()
        assert len(deliveries) == 1


def test_version_change_without_notify_records_event_only(client, app, monkeypatch):
    login(client)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.0")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with session_scope(app.state.session_factory) as db:
        _seed_fingerprint(db, "2.0.0@aaaaaaa")
        _set_notify_on_update(db, False)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.2")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    with session_scope(app.state.session_factory) as db:
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        assert db.query(Event).filter_by(type="system.updated").count() == 1
        assert db.query(WebhookDelivery).count() == 0


def test_same_version_restart_is_silent(client, app, monkeypatch):
    login(client)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.1.0")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "cccccccccccccccccccccccccccccccccccccccc")
    with session_scope(app.state.session_factory) as db:
        _seed_fingerprint(db, "2.1.0@ccccccc")
        _set_notify_on_update(db, True)
    with session_scope(app.state.session_factory) as db:
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        assert db.query(Event).filter_by(type="system.updated").count() == 0


def test_delivery_error_does_not_raise(client, app, monkeypatch):
    login(client)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.0")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "dddddddddddddddddddddddddddddddddddddddd")
    with session_scope(app.state.session_factory) as db:
        _seed_fingerprint(db, "2.0.0@ddddddd")
        _set_notify_on_update(db, True)

    def boom(*_args, **_kwargs):
        raise RuntimeError("canal indisponible")

    monkeypatch.setattr("mediatracker.events.deliver.schedule_deliveries", boom)
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.0.3")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee")
    with session_scope(app.state.session_factory) as db:
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        assert db.get(Setting, FINGERPRINT_KEY).value == "2.0.3@eeeeeee"
        assert db.query(Event).filter_by(type="system.updated").count() == 1
