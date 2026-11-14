"""TMDB image disk cache and proxy route."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from tests.conftest import csrf, login

from mediatracker.core.outbound_http import close_outbound_http_clients
from mediatracker.integrations.tmdb_images import (
    ImageCacheError,
    cache_file,
    cache_root,
    maybe_purge_cache,
    purge_cache,
    resolve_cached,
    touch_if_stale,
    validate_filename,
)
from mediatracker.security.sessions import SESSION_COOKIE


@pytest.fixture(autouse=True)
def _reset_http_pool():
    close_outbound_http_clients()
    yield
    close_outbound_http_clients()


def test_filename_validation_rejects_traversal():
    with pytest.raises(ImageCacheError):
        validate_filename("../secret.jpg")
    with pytest.raises(ImageCacheError):
        validate_filename("nested/evil.jpg")
    assert validate_filename("affiche-exemple.jpg") == "affiche-exemple.jpg"


def test_resolve_cached_fetches_once_and_reuses(settings, monkeypatch):
    calls = 0

    def _download(size: str, filename: str) -> tuple[bytes, str | None]:
        nonlocal calls
        calls += 1
        assert size == "w342"
        assert filename == "affiche-exemple.jpg"
        return b"jpeg-bytes", "image/jpeg"

    monkeypatch.setattr("mediatracker.integrations.tmdb_images._download", _download)
    first = resolve_cached(settings, "poster", "w342", "affiche-exemple.jpg")
    second = resolve_cached(settings, "poster", "w342", "affiche-exemple.jpg")
    assert first.path.is_file()
    assert first.path == second.path
    assert calls == 1
    assert "secret" not in str(first.path)
    assert "tmdb" not in first.path.read_bytes().decode("latin-1", errors="ignore")


def test_purge_removes_stale_and_enforces_cap(settings):
    root = cache_root(settings)
    old = cache_file(root, "poster", "w92", "vieux.jpg")
    fresh = cache_file(root, "poster", "w92", "frais.jpg")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"x" * 100)
    fresh.write_bytes(b"y" * 100)
    moment = datetime(2026, 6, 1, tzinfo=timezone.utc)
    old_time = (moment - timedelta(days=90)).timestamp()
    fresh_time = moment.timestamp()
    import os

    os.utime(old, (old_time, old_time))
    os.utime(fresh, (fresh_time, fresh_time))
    settings.image_cache_max_bytes = 150
    settings.image_cache_stale_days = 60
    report = purge_cache(settings, now=moment)
    assert report.removed_stale == 1
    assert not old.is_file()
    assert fresh.is_file()


def test_touch_if_stale_skips_files_read_within_a_day(settings):
    path = cache_file(cache_root(settings), "poster", "w92", "recent.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    moment = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    recent = (moment - timedelta(hours=2)).timestamp()
    os.utime(path, (recent, recent))
    assert touch_if_stale(path, now=moment) is False
    assert path.stat().st_mtime == recent
    old = (moment - timedelta(hours=25)).timestamp()
    os.utime(path, (old, old))
    assert touch_if_stale(path, now=moment) is True
    assert path.stat().st_mtime >= old


def test_image_route_does_not_touch_fresh_files(client, settings):
    path = cache_file(cache_root(settings), "poster", "w185", "frais.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"cached")
    recent = time.time() - 3600
    os.utime(path, (recent, recent))
    before = path.stat().st_mtime
    login(client)
    response = client.get("/api/v1/images/poster/w185/frais.jpg")
    assert response.status_code == 200, response.text
    assert path.stat().st_mtime == before


def test_image_route_auth_via_session_cookie_like_img_tag(client, settings):
    path = cache_file(cache_root(settings), "poster", "w185", "cookie.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"from-cache")
    login(client)
    session = client.cookies.get(SESSION_COOKIE)
    assert session
    with TestClient(client.app) as img_client:
        img_client.cookies.set(SESSION_COOKIE, session)
        response = img_client.get("/api/v1/images/poster/w185/cookie.jpg")
    assert response.status_code == 200
    assert response.content == b"from-cache"
    assert "authorization" not in response.request.headers


def test_image_route_serves_cache_headers_and_304(client, monkeypatch):
    calls = 0

    def _download(size: str, filename: str) -> tuple[bytes, str | None]:
        nonlocal calls
        calls += 1
        del size, filename
        return b"poster-data", "image/jpeg"

    monkeypatch.setattr("mediatracker.integrations.tmdb_images._download", _download)
    login(client)
    url = "/api/v1/images/poster/w185/affiche-exemple.jpg"
    first = client.get(url)
    assert first.status_code == 200, first.text
    assert first.content == b"poster-data"
    assert first.headers["cache-control"] == "public, max-age=31536000, immutable"
    etag = first.headers["etag"]
    second = client.get(url, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag
    assert calls == 1


def test_image_route_rejects_bad_kind(client):
    login(client)
    denied = client.get("/api/v1/images/evil/w92/x.jpg")
    assert denied.status_code == 404


def test_purge_endpoint_requires_write(client):
    login(client)
    denied = client.post("/api/v1/images/purge")
    assert denied.status_code == 403
    ok = client.post("/api/v1/images/purge", headers=csrf(client))
    assert ok.status_code == 200
    body = ok.json()
    assert "bytes_freed" in body


def test_maybe_purge_runs_once_per_day(client, settings):
    db = client.app.state.session_factory()
    try:
        root = cache_root(settings)
        path = cache_file(root, "poster", "w92", "once.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"z")
        moment = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        first = maybe_purge_cache(settings, db, now=moment)
        assert first is not None
        db.commit()
        second = maybe_purge_cache(settings, db, now=moment + timedelta(hours=2))
        assert second is None
    finally:
        db.close()
