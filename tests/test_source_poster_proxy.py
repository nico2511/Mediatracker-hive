"""SSRF policy, size and type limits for the source poster proxy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from tests.conftest import csrf, login

from mediatracker.core.source_poster_cache import (
    _FAIL_TTL,
    _TRANSIENT_FAIL_TTL,
    SourcePosterError,
    _remember_fail,
    enforce_poster_fetch_url,
    fetch_source_poster,
    normalize_source_poster_url,
    resolve_cached_source_poster,
)
from mediatracker.db.models import CatalogEntry, Provider, Setting

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
_ALLOWED = frozenset({"image.tmdb.org", "wawa.test"})


def _public_dns(*args, **kwargs):
    host = args[0] if args else kwargs.get("host", "")
    if host in {"wawa.test", "image.tmdb.org"}:
        return [(2, 1, 6, "", ("93.184.216.34", 0))]
    return []


def test_enforce_poster_rejects_disallowed_host():
    with pytest.raises(SourcePosterError, match="non autorisé"):
        enforce_poster_fetch_url("https://evil.example.test/poster.jpg", _ALLOWED)


def test_enforce_poster_rejects_private_literal_ip():
    with pytest.raises(SourcePosterError):
        enforce_poster_fetch_url("http://192.168.1.20/poster.jpg", _ALLOWED)


def test_enforce_poster_rejects_private_resolved_host():
    def _fake_resolve(host, port, family=0, type=0):
        if host == "wawa.test":
            return [(family, type, 0, "", ("192.168.1.20", port))]
        return []

    with patch("mediatracker.core.source_poster_cache.socket.getaddrinfo", side_effect=_fake_resolve):
        with pytest.raises(SourcePosterError, match="réseau"):
            enforce_poster_fetch_url("https://wawa.test/poster.webp", _ALLOWED)


def test_fetch_source_poster_rejects_oversized_body():
    class _Stream:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *args):
            return False

    class _Client:
        def stream(self, method, url, headers=None):
            chunk = b"x" * (5 * 1024 * 1024 + 1)

            class _Resp:
                status_code = 200
                headers = {}

                def iter_bytes(self):
                    yield chunk

            return _Stream(_Resp())

        def close(self):
            return None

    with (
        patch("mediatracker.core.source_poster_cache.socket.getaddrinfo", side_effect=_public_dns),
        patch(
            "mediatracker.core.source_poster_cache.borrow_http_client",
            return_value=(_Client(), True),
        ),
    ):
        with pytest.raises(SourcePosterError, match="volumineuse"):
            fetch_source_poster(None, "https://wawa.test/big.webp", allowed_hosts=_ALLOWED)


def test_fetch_source_poster_rejects_unknown_magic():
    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        status_code = 200
        headers = {}

        def iter_bytes(self):
            yield b"not-an-image"

    class _Client:
        def stream(self, method, url, headers=None):
            return _Stream()

        def close(self):
            return None

    with (
        patch("mediatracker.core.source_poster_cache.socket.getaddrinfo", side_effect=_public_dns),
        patch(
            "mediatracker.core.source_poster_cache.borrow_http_client",
            return_value=(_Client(), True),
        ),
    ):
        with pytest.raises(SourcePosterError, match="Format"):
            fetch_source_poster(None, "https://wawa.test/x.webp", allowed_hosts=_ALLOWED)


def test_normalize_rejects_absolute_url_on_foreign_host(client, app):
    db = app.state.session_factory()
    try:
        base = db.get(Setting, "wawacity_base_url")
        if base is None:
            db.add(Setting(key="wawacity_base_url", value="https://wawa.test", is_secret=False, updated_at=NOW))
        else:
            base.value = "https://wawa.test"
        row = CatalogEntry(
            dedupe_key="abs|2024|film||",
            title="Exemple film",
            title_folded="exemple film",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film",
            source_poster_url="https://evil.example.test/cover.jpg",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(row)
        db.commit()
        assert (
            normalize_source_poster_url(db, app.state.settings, row, row.source_poster_url) is None
        )
    finally:
        db.close()


def test_fetch_accepts_valid_webp_magic():
    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        status_code = 200
        headers = {}

        def iter_bytes(self):
            yield _WEBP

    class _Client:
        def stream(self, method, url, headers=None):
            return _Stream()

        def close(self):
            return None

    with (
        patch("mediatracker.core.source_poster_cache.socket.getaddrinfo", side_effect=_public_dns),
        patch(
            "mediatracker.core.source_poster_cache.borrow_http_client",
            return_value=(_Client(), True),
        ),
    ):
        body, media_type = fetch_source_poster(None, "https://wawa.test/ok.webp", allowed_hosts=_ALLOWED)
        assert media_type == "image/webp"
        assert body == _WEBP


def test_normalize_rebases_absolute_url_on_stale_host(client, app):
    db = app.state.session_factory()
    try:
        base = db.get(Setting, "wawacity_base_url")
        if base is None:
            db.add(Setting(key="wawacity_base_url", value="https://wawa.test", is_secret=False, updated_at=NOW))
        else:
            base.value = "https://wawa.test"
        row = CatalogEntry(
            dedupe_key="rebase|2024|film||",
            title="Exemple film",
            title_folded="exemple film",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film",
            source_poster_url="https://legacy.example.test/img/old.webp",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(row)
        db.commit()
        normalized = normalize_source_poster_url(db, app.state.settings, row, row.source_poster_url)
        assert normalized == "https://wawa.test/img/old.webp"
    finally:
        db.close()


def test_normalize_uses_provider_config_base_url(client, app):
    from sqlalchemy import select

    db = app.state.session_factory()
    try:
        setting = db.get(Setting, "wawacity_base_url")
        if setting is None:
            db.add(Setting(key="wawacity_base_url", value="https://setting.test", is_secret=False, updated_at=NOW))
        else:
            setting.value = "https://setting.test"
        provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "wawacity"))
        if provider is None:
            db.add(
                Provider(
                    kind="source",
                    type="wawacity",
                    name="Wawacity",
                    enabled=True,
                    config={"base_url": "https://provider.test"},
                )
            )
        else:
            provider.config = {"base_url": "https://provider.test"}
        row = CatalogEntry(
            dedupe_key="provider|2024|film||",
            title="Exemple film",
            title_folded="exemple film",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://provider.test/film",
            source_poster_url="/covers/from-provider.jpg",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(row)
        db.commit()
        normalized = normalize_source_poster_url(db, app.state.settings, row, row.source_poster_url)
        assert normalized == "https://provider.test/covers/from-provider.jpg"
    finally:
        db.close()


def test_poster_fetch_headers_use_origin_referer(client, app):
    from mediatracker.core.source_poster_cache import _poster_fetch_headers

    db = app.state.session_factory()
    try:
        setting = db.get(Setting, "wawacity_base_url")
        if setting is None:
            db.add(Setting(key="wawacity_base_url", value="https://wawa.test/path/", is_secret=False, updated_at=NOW))
        else:
            setting.value = "https://wawa.test/path/"
        row = CatalogEntry(
            dedupe_key="hdr|2024|film||",
            title="Exemple film",
            title_folded="exemple film",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        headers = _poster_fetch_headers(db, app.state.settings, row)
        assert headers.get("Referer") == "https://wawa.test/"
        assert "User-Agent" in headers
    finally:
        db.close()


def test_fetch_source_poster_passes_headers_per_request():
    seen: list[dict[str, str]] = []

    class _Stream:
        def __init__(self, response):
            self._response = response

        def __enter__(self):
            return self._response

        def __exit__(self, *args):
            return False

    class _Resp:
        status_code = 200
        headers = {}

        def iter_bytes(self):
            yield _WEBP

    class _Client:
        def stream(self, method, url, headers=None):
            seen.append(dict(headers or {}))
            return _Stream(_Resp())

        def close(self):
            return None

    with (
        patch("mediatracker.core.source_poster_cache.socket.getaddrinfo", side_effect=_public_dns),
        patch(
            "mediatracker.core.source_poster_cache.borrow_http_client",
            return_value=(_Client(), True),
        ),
    ):
        fetch_source_poster(
            None,
            "https://wawa.test/ok.webp",
            allowed_hosts=_ALLOWED,
            headers={"Referer": "https://wawa.test/", "User-Agent": "Agent Démo"},
        )
    assert seen == [{"Referer": "https://wawa.test/", "User-Agent": "Agent Démo"}]


def test_remember_fail_uses_short_ttl_for_transient_errors(tmp_path):
    fail = tmp_path / "1.fail"
    moment = NOW
    _remember_fail(fail, moment, code="TRANSPORT")
    expires = datetime.fromisoformat(fail.read_text(encoding="utf-8").strip())
    delta = expires - moment
    assert abs(delta - _TRANSIENT_FAIL_TTL) < timedelta(seconds=2)
    _remember_fail(fail, moment, code="NOT_FOUND")
    expires = datetime.fromisoformat(fail.read_text(encoding="utf-8").strip())
    delta = expires - moment
    assert abs(delta - _FAIL_TTL) < timedelta(seconds=2)


def test_resolve_waits_on_entry_lock(client, app, monkeypatch):
    import threading

    from mediatracker.core.source_poster_cache import _entry_lock

    db = app.state.session_factory()
    setting = db.get(Setting, "wawacity_base_url")
    if setting is None:
        db.add(Setting(key="wawacity_base_url", value="https://wawa.test", is_secret=False, updated_at=NOW))
    else:
        setting.value = "https://wawa.test"
    row = CatalogEntry(
        dedupe_key="lock|2024|film||",
        title="Exemple lock",
        title_folded="exemple lock",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url="https://wawa.test/film/lock",
        source_poster_url="/img/films/lock.webp",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    db.commit()
    lock = _entry_lock(row.id)
    assert lock.acquire(blocking=False)
    calls = {"n": 0}

    def _fake_fetch(*_args, **_kwargs):
        calls["n"] += 1
        return _WEBP, "image/webp"

    monkeypatch.setattr("mediatracker.core.source_poster_cache.fetch_source_poster", _fake_fetch)
    monkeypatch.setattr(
        "mediatracker.core.source_poster_cache._write_cache_atomic",
        lambda *args, **kwargs: None,
    )

    def release():
        lock.release()

    timer = threading.Timer(0.05, release)
    timer.start()
    try:
        resolve_cached_source_poster(db, app.state.settings, row)
    finally:
        timer.cancel()
    assert calls["n"] == 1
    db.close()


def test_source_fallback_title_card_is_no_store(client, app):
    db = app.state.session_factory()
    row = CatalogEntry(
        dedupe_key="svg|2024|film||",
        title="Exemple SVG",
        title_folded="exemple svg",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url="https://wawa.test/film/svg",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    db.commit()
    login(client)
    response = client.get(f"/api/v1/images/source/{row.id}", headers=csrf(client))
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("image/svg")
    assert response.headers.get("cache-control") == "no-store"
    db.close()
