"""S3h — jeton webhook Jellyfin en en-tête (X-MediaTracker-Token)."""

import logging
from datetime import datetime, timedelta, timezone

from tests.conftest import csrf, login

from mediatracker.db.models import Setting
from mediatracker.integrations.jellyfin_webhook_auth import (
    WEBHOOK_QUERY_TOKEN_LAST_AT,
    reset_query_warn_cache_for_tests,
)

_WEBHOOK_SECRET = "webhook-secret-value"


def _configure_jellyfin(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.local:8096",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": "user-1",
                "jellyfin_webhook_secret": _WEBHOOK_SECRET,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _webhook_payload() -> dict:
    return {
        "NotificationType": "PlaybackStop",
        "ItemType": "Episode",
        "PlayedToCompletion": True,
    }


def test_webhook_accepts_header_token(client):
    login(client)
    _configure_jellyfin(client)
    response = client.post(
        "/api/v1/integrations/jellyfin/webhook",
        headers={"X-MediaTracker-Token": _WEBHOOK_SECRET},
        json=_webhook_payload(),
    )
    assert response.status_code == 200, response.text


def test_webhook_header_priority_over_query(client):
    login(client)
    _configure_jellyfin(client)
    response = client.post(
        "/api/v1/integrations/jellyfin/webhook?token=nope",
        headers={"X-MediaTracker-Token": _WEBHOOK_SECRET},
        json=_webhook_payload(),
    )
    assert response.status_code == 200, response.text


def test_webhook_invalid_query_does_not_record_usage(client, caplog):
    reset_query_warn_cache_for_tests()
    login(client)
    _configure_jellyfin(client)
    with caplog.at_level(logging.WARNING, logger="mediatracker.integrations.jellyfin_webhook"):
        response = client.post(
            "/api/v1/integrations/jellyfin/webhook?token=nope",
            json=_webhook_payload(),
        )
    assert response.status_code == 401
    assert "paramètre d'URL" not in caplog.text
    components = client.get("/api/v1/health/components")
    assert components.status_code == 200, components.text
    jellyfin = next(row for row in components.json()["components"] if row["name"] == "jellyfin")
    assert "jeton dans l'URL" not in jellyfin["message"]


def test_webhook_invalid_header_returns_401_even_with_valid_query(client):
    login(client)
    _configure_jellyfin(client)
    response = client.post(
        f"/api/v1/integrations/jellyfin/webhook?token={_WEBHOOK_SECRET}",
        headers={"X-MediaTracker-Token": "nope"},
        json=_webhook_payload(),
    )
    assert response.status_code == 401


def test_webhook_query_records_health_warning_and_logs_without_secret(client, caplog):
    reset_query_warn_cache_for_tests()
    login(client)
    _configure_jellyfin(client)
    with caplog.at_level(logging.WARNING, logger="mediatracker.integrations.jellyfin_webhook"):
        called = client.post(
            f"/api/v1/integrations/jellyfin/webhook?token={_WEBHOOK_SECRET}",
            json=_webhook_payload(),
        )
    assert called.status_code == 200, called.text
    assert _WEBHOOK_SECRET not in caplog.text
    assert "paramètre d'URL" in caplog.text

    components = client.get("/api/v1/health/components")
    assert components.status_code == 200, components.text
    jellyfin = next(row for row in components.json()["components"] if row["name"] == "jellyfin")
    assert jellyfin["status"] == "degraded"
    assert "jeton dans l'URL" in jellyfin["message"]
    assert "dernier usage" in jellyfin["message"].lower()


def test_webhook_query_warning_log_is_rate_limited_per_token(client, caplog):
    reset_query_warn_cache_for_tests()
    login(client)
    _configure_jellyfin(client)
    payload = _webhook_payload()
    with caplog.at_level(logging.WARNING, logger="mediatracker.integrations.jellyfin_webhook"):
        for _ in range(3):
            response = client.post(
                f"/api/v1/integrations/jellyfin/webhook?token={_WEBHOOK_SECRET}",
                json=payload,
            )
            assert response.status_code == 200, response.text
    assert caplog.text.count("paramètre d'URL") == 1
    assert _WEBHOOK_SECRET not in caplog.text


def test_webhook_query_health_warning_expires_after_seven_days(client, app):
    login(client)
    _configure_jellyfin(client)
    stale = datetime.now(timezone.utc) - timedelta(days=8)
    db = app.state.session_factory()
    try:
        db.add(
            Setting(
                key=WEBHOOK_QUERY_TOKEN_LAST_AT,
                value=stale.isoformat(),
                is_secret=False,
                updated_at=stale,
            )
        )
        db.commit()
    finally:
        db.close()
    components = client.get("/api/v1/health/components")
    assert components.status_code == 200, components.text
    jellyfin = next(row for row in components.json()["components"] if row["name"] == "jellyfin")
    assert "jeton dans l'URL" not in jellyfin["message"]
