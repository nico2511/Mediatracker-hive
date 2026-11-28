"""API carte réseau : graphe statique et activité."""

from tests.conftest import login

from mediatracker.metro.collection import set_collection_duration
from mediatracker.metro.graph import STATIONS
from mediatracker.metro.record import (
    flush_pass_buffer,
    load_pass_collection_flag,
    record_task_run,
    reset_metro_instrumentation_state,
)
from mediatracker.metro.ui_graph import VISIBLE_STATION_IDS


def test_metro_graph_payload(client, app):
    login(client)
    resp = client.get("/api/v1/metro/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["station_count_visible"] == len(VISIBLE_STATION_IDS)
    assert body["station_count_total"] == len(STATIONS)
    assert len(body["edges"]) >= 28
    assert body["retention_days"] == 7
    assert body.get("diagnostics") == []


def test_metro_activity_empty(client, app):
    login(client)
    with app.state.session_factory() as db:
        set_collection_duration(db, "off")
        db.commit()
    reset_metro_instrumentation_state()
    resp = client.get("/api/v1/metro/activity?hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is False
    assert "graph" in body
    assert body["collection"]["active"] is False


def test_metro_activity_with_runs(client, app):
    login(client)
    with app.state.session_factory() as db:
        set_collection_duration(db, "1h")
        db.commit()
    reset_metro_instrumentation_state()
    factory = app.state.session_factory
    load_pass_collection_flag(factory)
    record_task_run(station="RNG5", trigger="event", outcome="ok", duration_ms=1800, edge_id="C16")
    record_task_run(
        station="DL1",
        trigger="schedule",
        outcome="blocked",
        blocked_by="ADB1:AUTH_BLOCKED",
        edge_id="C06",
    )
    flush_pass_buffer(factory)
    resp = client.get("/api/v1/metro/activity?hours=24")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is True
    assert "RNG5" in body["station_stats"]
    assert isinstance(body["level1_edges"], list)
    assert "C06" in body["edge_details"]
    assert body["edge_details"]["C06"]["timeline_24h"]
    assert body["edge_details"]["C06"]["timeline_3h"]
    assert "RNG5" in body["station_details"]
    assert body["station_details"]["RNG5"]["outbound"]
    diagnostics = body["graph"]["diagnostics"]
    assert not any("42 min" in str(row.get("title", "")) for row in diagnostics)
    assert any(row.get("tag") == "Blocage" for row in diagnostics)
    assert "C16" in body["rng5_exit_stats"]
    assert body["rng5_exit_stats"]["C16"]["passes_24h"] >= 1
    bucket = body["edge_details"]["C16"]["timeline_24h"][0]
    assert "median_ms" in bucket