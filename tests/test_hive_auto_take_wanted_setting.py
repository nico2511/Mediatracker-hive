"""Réglage hive_auto_take_wanted : persistance et exposition API."""

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.db.models import Setting


def test_hive_auto_take_wanted_default_false(client):
    login(client)
    body = client.get("/api/v1/settings").json()
    by_key = {row["key"]: row for row in body["settings"]}
    assert by_key["hive_auto_take_wanted"]["value"] is False
    assert by_key["hive_auto_take_wanted"]["default"] is False


def test_hive_auto_take_wanted_patch_roundtrip(client):
    login(client)
    on = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_auto_take_wanted": True}},
        headers=csrf(client),
    )
    assert on.status_code == 200, on.text
    off = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_auto_take_wanted": False}},
        headers=csrf(client),
    )
    assert off.status_code == 200, off.text

    db = client.app.state.session_factory()
    try:
        row = db.scalar(select(Setting).where(Setting.key == "hive_auto_take_wanted"))
        assert row is not None
        assert row.value == "false"
    finally:
        db.close()