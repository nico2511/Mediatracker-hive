"""Constats Activité : pas de scénario fictif dans l'API."""

from tests.conftest import login

from mediatracker.metro.diagnostics import build_activity_diagnostics


def test_build_diagnostics_block_from_collecte(client, app):
    login(client)
    rows = build_activity_diagnostics(
        active_blocks=[
            {
                "station_id": "DL1",
                "blocked_by": "ADB1:AUTH_BLOCKED",
                "edge_id": "C06",
                "minutes": 9,
            }
        ],
        slow_stations=[],
        station_stats={},
    )
    assert rows[0]["tag"] == "Blocage"
    assert "9 min" in rows[0]["title"]
    assert "AllDebrid bloque la file depuis 42 min" not in str(rows)


def test_activity_diagnostics_absent_when_no_collecte(client, app):
    login(client)
    from mediatracker.metro.collection import set_collection_duration
    from mediatracker.metro.record import reset_metro_instrumentation_state

    with app.state.session_factory() as db:
        set_collection_duration(db, "off")
        db.commit()
    reset_metro_instrumentation_state()
    resp = client.get("/api/v1/metro/activity?hours=24")
    body = resp.json()
    titles = [row.get("title", "") for row in body["graph"]["diagnostics"]]
    assert not any("depuis 42 min" in title for title in titles)