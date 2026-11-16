"""Fournisseurs retirés du registre mais encore présents en base."""

from tests.conftest import login

from mediatracker.db.models import Provider
from mediatracker.db.session import session_scope


def test_list_providers_skips_unregistered_legacy_type(client, app, caplog):
    login(client)
    with session_scope(app.state.session_factory) as db:
        db.add(
            Provider(
                kind="source",
                type="legacy_retired_source",
                name="Legacy",
                enabled=False,
                priority=0,
                config={},
                builtin=False,
            )
        )
        db.commit()
    with caplog.at_level("INFO", logger="mediatracker.api.v1.providers"):
        response = client.get("/api/v1/providers?kind=source")
    assert response.status_code == 200, response.text
    types = {item["type"] for item in response.json()["items"]}
    assert "legacy_retired_source" not in types
    assert any("Fournisseur ignoré" in record.message for record in caplog.records)