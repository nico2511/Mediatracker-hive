"""Notifier payloads, HMAC, outbox retry, dedupe, grouping, and quiet hours."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
from tests.conftest import csrf, login

from mediatracker.db.models import Event, Media, MediaItem, Setting, WebhookDelivery
from mediatracker.events import record_event
from mediatracker.events.deliver import deliver_due, quiet_deadline
from mediatracker.events.homeassistant import format_homeassistant
from mediatracker.events.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_body
from mediatracker.events.text import notification_text


def test_hmac_signs_timestamp_and_body():
    body = b'{"event":"library.imported"}'
    secret = "topsecret"
    stamp = "1700000000"
    signed = sign_body(secret, stamp, body)
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert signed == f"sha256={digest}"
    assert sign_body(secret, "1700000001", body) != signed
    assert sign_body(secret, stamp, body + b" ") != signed


def test_home_assistant_contract_and_french_text():
    payload = {
        "title": "Exemple",
        "media_type": "series",
        "media_id": 1,
        "item_id": 2,
        "tmdb_id": 99,
        "season": 1,
        "episode": 1,
        "episode_title": "Pilote",
        "path": "/data/media/Series/Exemple/S01E01.mkv",
        "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg",
        "page_url": "http://nas.local:8080/medias/1",
        "level": "info",
        "secret": "ne-doit-pas-sortir",
        "error": "voir https://rapidgator.net/file/abc",
    }
    body = format_homeassistant("library.imported", payload)
    assert body["event"] == "library.imported"
    assert body["title"] == "Nouvel épisode"
    assert body["message"] == "Exemple — S01E01 est disponible sur le NAS."
    assert body["poster_url"] == payload["poster_url"]
    assert body["media_id"] == 1
    assert body["tmdb_id"] == 99
    assert body["season"] == 1
    assert body["episode_number"] == 1
    assert body["url"] == "http://nas.local:8080/medias/1"
    assert body["media"]["id"] == 1
    assert body["media"]["tmdb_id"] == 99
    assert body["episode"]["id"] == 2
    assert body["episode"]["season"] == 1
    assert body["episode"]["number"] == 1
    assert body["episode"]["title"] == "Pilote"
    assert body["actions"][0]["url"] == body["url"]
    assert body["tag"] == "mediatracker-library.imported-1-2-"
    dumped = json.dumps(body)
    assert "ne-doit-pas-sortir" not in dumped
    assert "rapidgator" not in dumped
    assert "alldebrid" not in dumped
    title, message = notification_text(
        "debrid.auth_blocked",
        {"message": "Nouvelle adresse IP bloquée par AllDebrid (AUTH_BLOCKED). Confirmation e-mail requise."},
    )
    assert title == "AllDebrid bloqué"
    assert "e-mail" in message
    assert quiet_deadline(
        datetime(2026, 10, 11, 23, tzinfo=timezone.utc),
        "22:00",
        "08:00",
        "UTC",
    ) == datetime(2026, 10, 12, 8, tzinfo=timezone.utc)
    assert quiet_deadline(datetime(2026, 10, 11, 12, tzinfo=timezone.utc), "22:00", "08:00", "UTC") is None


def test_outbox_retries_dedupes_and_groups(client, app, monkeypatch):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"notify_group_seconds": 0, "public_base_url": "http://nas.local:8080"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "hmac",
            "url": "https://hooks.example/mt",
            "type": "generic",
            "secret": "hmac-secret",
            "events": ["download.failed", "library.imported"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    calls: list[dict] = []

    def send(url, body, headers):
        calls.append({"url": url, "body": body, "headers": dict(headers)})
        return 500 if len(calls) == 1 else 200

    monkeypatch.setattr("mediatracker.events.deliver.post_request", send)
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Exemple", tmdb_id=99, poster_url="https://image.tmdb.org/t/p/w500/x.jpg")
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, title="Pilote", state="missing")
        db.add(item)
        db.flush()
        first = record_event(
            db,
            "download.failed",
            level="warning",
            media_id=media.id,
            item_id=item.id,
            dedupe_key="download.failed:9",
            when=now,
            payload={
                "title": "Exemple",
                "media_id": media.id,
                "item_id": item.id,
                "tmdb_id": 99,
                "season": 1,
                "episode": 1,
                "error": "échec définitif",
                "poster_url": media.poster_url,
                "level": "warning",
            },
        )
        again = record_event(
            db,
            "download.failed",
            dedupe_key="download.failed:9",
            when=now,
            payload={"title": "doublon"},
        )
        assert again.id == first.id
        db.commit()
        assert deliver_due(db, app.state.settings, now) == 1
        assert len(calls) == 1
        assert calls[0]["headers"][TIMESTAMP_HEADER]
        stamp = calls[0]["headers"][TIMESTAMP_HEADER]
        assert calls[0]["headers"][SIGNATURE_HEADER] == sign_body("hmac-secret", stamp, calls[0]["body"])
        assert b"hmac-secret" not in calls[0]["body"]
        row = db.query(WebhookDelivery).filter_by(event_id=first.id).one()
        assert row.status == "pending"
        assert row.attempts == 1
        assert deliver_due(db, app.state.settings, now + timedelta(seconds=30)) == 0
        assert len(calls) == 1
        assert deliver_due(db, app.state.settings, now + timedelta(minutes=1)) == 1
        db.refresh(row)
        assert row.status == "sent"
        deliveries = db.query(WebhookDelivery).filter_by(event_id=first.id).count()
        assert deliveries == 1

        calls.clear()
        group = db.get(Setting, "notify_group_seconds")
        assert group is not None
        group.value = "120"
        db.commit()
        record_event(
            db,
            "library.imported",
            media_id=media.id,
            item_id=item.id,
            when=now,
            payload={"title": "Exemple", "media_id": media.id, "season": 1, "episode": 1, "tmdb_id": 99},
        )
        record_event(
            db,
            "library.imported",
            media_id=media.id,
            item_id=item.id,
            when=now + timedelta(seconds=30),
            payload={"title": "Exemple", "media_id": media.id, "season": 1, "episode": 2, "tmdb_id": 99},
        )
        db.commit()
        assert deliver_due(db, app.state.settings, now + timedelta(seconds=10)) == 0
        assert deliver_due(db, app.state.settings, now + timedelta(seconds=120)) == 1
        grouped = json.loads(calls[-1]["body"])
        assert grouped["grouped"] is True
        assert grouped["count"] == 2
        assert grouped["title"] == "Fichiers rangés"
        assert "hmac-secret" not in calls[-1]["body"].decode()
    finally:
        db.close()


def test_quiet_hours_defer_delivery_but_not_the_test_button(client, app, monkeypatch):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "notify_group_seconds": 0,
                "notify_quiet_start": "22:00",
                "notify_quiet_end": "08:00",
                "notify_timezone": "UTC",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "ha",
            "url": "http://homeassistant.local:8123/api/webhook/mediatracker",
            "type": "homeassistant",
            "events": ["item.available"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    calls: list[bytes] = []

    def send(url, body, headers):
        del url, headers
        calls.append(body)
        return 204

    monkeypatch.setattr("mediatracker.events.deliver.post_request", send)
    now = datetime(2026, 10, 11, 23, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        record_event(
            db,
            "item.available",
            when=now,
            payload={"title": "Exemple", "media_id": 1, "season": 1, "episode": 2, "media_type": "series"},
        )
        db.commit()
        assert deliver_due(db, app.state.settings, now) == 0
        assert calls == []
        row = db.query(WebhookDelivery).one()
        assert row.next_attempt_at is not None
        assert deliver_due(db, app.state.settings, datetime(2026, 10, 12, 8, tzinfo=timezone.utc)) == 1
        sent = json.loads(calls[0])
        assert sent["event"] == "item.available"
        assert "téléchargé" in sent["message"]
        assert "mediatracker" not in sent["message"]
    finally:
        db.close()
    tested = client.post(f"/api/v1/webhooks/{created.json()['id']}/test", headers=csrf(client))
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True
    probe = json.loads(calls[-1])
    assert probe["event"] == "notify.test"
    assert probe["title"] == "Test MediaTracker"
    assert tested.json()["message"].startswith("Notification de test envoyée (HTTP ")
    kinds = client.get("/api/v1/events/notifications")
    assert kinds.status_code == 200
    labels = {row["type"]: row["label"] for row in kinds.json()["events"]}
    assert labels["debrid.auth_blocked"].startswith("AllDebrid")
    assert "cleanup.upcoming" in labels
    stored = db = app.state.session_factory()
    try:
        assert stored.query(Event).filter_by(type="notify.test").count() == 0
    finally:
        stored.close()


def test_existing_webhook_test_names_the_transport_error(client, monkeypatch):
    login(client)
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "Maison",
            "url": "http://homeassistant.local:8123/api/webhook/mediatracker",
            "type": "homeassistant",
            "events": ["library.imported"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    hook_id = created.json()["id"]

    def refuse(url, body, headers):
        del url, body, headers
        raise httpx.ConnectError("[Errno 111] Connection refused")

    monkeypatch.setattr("mediatracker.events.deliver.post_request", refuse)
    refused = client.post(f"/api/v1/webhooks/{hook_id}/test", headers=csrf(client))
    assert refused.status_code == 200, refused.text
    assert refused.json()["ok"] is False
    assert refused.json()["message"] == "Connexion refusée."

    def timeout(url, body, headers):
        del url, body, headers
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("mediatracker.events.deliver.post_request", timeout)
    late = client.post(f"/api/v1/webhooks/{hook_id}/test", headers=csrf(client))
    assert late.json()["ok"] is False
    assert late.json()["message"] == "Délai dépassé."

    def http_status(url, body, headers):
        del url, body, headers
        return 404

    monkeypatch.setattr("mediatracker.events.deliver.post_request", http_status)
    missing = client.post(f"/api/v1/webhooks/{hook_id}/test", headers=csrf(client))
    assert missing.json() == {"ok": False, "status": 404, "message": "La cible a répondu HTTP 404."}
