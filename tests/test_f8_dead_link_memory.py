"""F8 — mémoire des liens morts et revérification hebdomadaire AllDebrid."""

import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from tests.conftest import csrf, login

from mediatracker.core.dead_links import (
    RECHECK_KEY,
    eligible_for_retry_revival,
    maybe_reverify_dead_links,
    revive_links_for_retry,
)
from mediatracker.core.pipeline.queue import retry_job
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider, Setting
from mediatracker.providers.debrid.alldebrid import AllDebridClient

NOW = datetime(2026, 10, 22, 14, 30, tzinfo=timezone.utc)


def _provider(db) -> Provider:
    return db.query(Provider).filter(Provider.type == "wawacity").one()


def _link_row(
    db,
    item_id: int,
    *,
    url: str = "https://rapidgator.net/file/brume-azur",
    dead: bool = True,
    dead_code: str | None = "LINK_DOWN",
    dead_at: datetime | None = None,
) -> Link:
    row = Link(
        media_item_id=item_id,
        provider_id=_provider(db).id,
        kind="ddl_hoster",
        url=url,
        hoster="rapidgator",
        external_id=f"ext-{url.rsplit('/', 1)[-1]}",
        priority=10,
        dead=dead,
        dead_code=dead_code,
        dead_at=dead_at or NOW,
        last_error="Lien hors ligne.",
    )
    db.add(row)
    return row


@patch("mediatracker.core.pipeline.queue.datetime")
def test_permanent_recent_link_stays_dead_on_retry(mock_dt, client, app):
    mock_dt.now.return_value = NOW
    mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
    login(client)
    media_id, item_id = _series(client)
    db = app.state.session_factory()
    try:
        link = _link_row(db, item_id, dead_code="LINK_DOWN", dead_at=NOW - timedelta(days=2))
        row = Download(media_item_id=item_id, state="failed", error_code="LINK_DOWN", created_at=NOW)
        db.add(row)
        db.commit()
        link_id = link.id
        download_id = row.id
    finally:
        db.close()

    retried = client.post(f"/api/v1/downloads/{download_id}/retry", headers=csrf(client))
    assert retried.status_code == 200, retried.text

    db = app.state.session_factory()
    try:
        assert db.get(Link, link_id).dead is True
        revived = revive_links_for_retry(db, item_id, NOW)
        assert revived == 0
    finally:
        db.close()


def test_transient_dead_link_is_revived_on_retry(client, app):
    login(client)
    media_id, item_id = _series(client)
    db = app.state.session_factory()
    try:
        link = _link_row(
            db,
            item_id,
            dead_code="LINK_HOST_UNAVAILABLE",
            dead_at=NOW - timedelta(hours=1),
        )
        link.last_error = "Hébergeur indisponible."
        row = Download(media_item_id=item_id, state="failed", created_at=NOW)
        db.add(row)
        db.commit()
        download_id = row.id
        link_id = link.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        retry_job(db, db.get(Download, download_id), app.state.settings)
        db.commit()
        assert db.get(Link, link_id).dead is False
    finally:
        db.close()


def test_old_permanent_dead_link_is_revived_on_retry(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Sable Nord", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, title="Pilot")
        db.add(item)
        db.flush()
        link = _link_row(
            db,
            item.id,
            dead_code="LINK_DOWN",
            dead_at=NOW - timedelta(days=8),
        )
        db.commit()
        assert eligible_for_retry_revival(link, NOW)
        revived = revive_links_for_retry(db, item.id, NOW)
        assert revived == 1
        assert link.dead is False
    finally:
        db.close()


def test_weekly_reverify_batches_link_infos_and_revives_online(client, app):
    del client
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/link/infos"):
            links = [
                urllib.parse.unquote_plus(value)
                for key, value in urllib.parse.parse_qsl(request.content.decode())
                if key == "link[]"
            ]
            calls.append(links)
            infos = []
            for link in links:
                online = "vivant" in link
                infos.append(
                    {
                        "link": link,
                        "filename": "film.mkv" if online else None,
                        "size": 1 if online else None,
                        "error": None if online else {"code": "LINK_DOWN", "message": "offline"},
                    }
                )
            return httpx.Response(200, json={"status": "success", "data": {"infos": infos}})
        return httpx.Response(404)

    client_ad = AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None)

    db = app.state.session_factory()
    try:
        media = Media(type="movie", title="Brume Azur", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, title="Film")
        db.add(item)
        db.flush()
        dead = _link_row(db, item.id, url="https://rapidgator.net/file/mort", dead_code="LINK_DOWN")
        alive = _link_row(db, item.id, url="https://turbobit.net/file/vivant", dead_code="LINK_DOWN")
        db.commit()
        dead_id = dead.id
        alive_id = alive.id
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        changed = maybe_reverify_dead_links(db, app.state.settings, now=NOW, debrid=client_ad)
        db.commit()
        assert changed is True
        assert len(calls) == 1
        assert set(calls[0]) == {
            "https://rapidgator.net/file/mort",
            "https://turbobit.net/file/vivant",
        }
        assert db.get(Link, dead_id).dead is True
        assert db.get(Link, alive_id).dead is False
        setting = db.get(Setting, RECHECK_KEY)
        assert setting is not None
    finally:
        db.close()

    db = app.state.session_factory()
    try:
        again = maybe_reverify_dead_links(db, app.state.settings, now=NOW + timedelta(days=2), debrid=client_ad)
        db.commit()
        assert again is False
        assert len(calls) == 1
    finally:
        db.close()


def test_redownload_keeps_recent_permanent_dead(client, app):
    login(client)
    media_id, item_id = _series(client, title="Rivage Ouest")
    db = app.state.session_factory()
    try:
        link = _link_row(
            db,
            item_id,
            url="https://turbobit.net/file/rivage-ouest",
            dead_code="LINK_DOWN",
            dead_at=NOW,
        )
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = "/tmp/ghost/Rivage.mkv"
        from mediatracker.core.forget import forget_local_file

        forget_local_file(db, db.get(Media, media_id), item, NOW)
        db.commit()
        link_id = link.id
    finally:
        db.close()

    with patch("mediatracker.core.source_poll.run_poll"):
        response = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert response.status_code == 200, response.text
    db = app.state.session_factory()
    try:
        assert db.get(Link, link_id).dead is True
    finally:
        db.close()


def _series(client, title: str = "Brume Azur") -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": title, "year": 2026, "tmdb_id": 77001},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2, "title": "Quai", "air_date": "2026-01-08"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]
