"""P6: one shared progress snapshot per second for every SSE client."""

import time

from tests.conftest import csrf, login


def _series(client):
    body = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série SSE", "tmdb_id": 99001},
        headers=csrf(client),
    )
    assert body.status_code == 201, body.text
    return body.json()


def test_shared_sse_one_db_read_per_tick_for_many_clients(client, app, monkeypatch):
    from mediatracker.api.v1.events import progress_snapshot_payload
    from mediatracker.api.v1.progress_stream import hub_for_app

    login(client)
    _series(client)
    reads = 0

    def counted(db):
        nonlocal reads
        reads += 1
        return progress_snapshot_payload(db)

    monkeypatch.setattr(
        "mediatracker.api.v1.events.progress_snapshot_payload",
        counted,
    )
    hub = hub_for_app(app)
    subs = [hub.subscribe(None, limit=2) for _ in range(3)]
    time.sleep(2.4)
    for sub in subs:
        hub.unsubscribe(sub)
    assert reads == 3
    assert reads < len(subs) * 3


def test_sse_includes_fiche_when_media_id_query(client, app):
    login(client)
    media = _series(client)
    app.state.sse_limit = 1
    response = client.get(f"/api/v1/events/stream?media_id={media['id']}")
    assert response.status_code == 200
    assert "fiches" in response.text
    assert str(media["id"]) in response.text
    assert "progress" in response.text


def test_sse_fiche_stream_never_commits(client, app, monkeypatch):
    from sqlalchemy.orm import Session

    from mediatracker.api.v1.progress_stream import hub_for_app

    login(client)
    media = _series(client)
    commits = 0
    original_commit = Session.commit

    def counted_commit(self, *args, **kwargs):
        nonlocal commits
        commits += 1
        return original_commit(self, *args, **kwargs)

    monkeypatch.setattr(Session, "commit", counted_commit)
    hub = hub_for_app(app)
    sub = hub.subscribe(media["id"], limit=2)
    time.sleep(2.2)
    hub.unsubscribe(sub)
    assert commits == 0


def test_hub_stops_after_last_client_disconnects(client, app):
    from mediatracker.api.v1.progress_stream import hub_for_app

    login(client)
    hub = hub_for_app(app)
    app.state.sse_limit = 1
    client.get("/api/v1/events/stream")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with hub._lock:
            if not hub._subscribers and hub._thread is None:
                break
        time.sleep(0.05)
    with hub._lock:
        assert not hub._subscribers
        assert hub._thread is None
