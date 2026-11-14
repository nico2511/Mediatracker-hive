"""W1/W2: source_binding.path and poll without redundant Wawacity search."""

from datetime import datetime, timedelta

import httpx
from tests.conftest import csrf, login
from tests.test_wawacity import BASE, NOW, _poll, _text

from mediatracker.core.source_binding import path_from_url, url_from_path
from mediatracker.db.models import Provider, SourceBinding

OLD_BASE = "https://ancien-domaine.test"
NEW_BASE = "https://nouveau-domaine.test"


def _is_search(url: str) -> bool:
    return "search=" in url


def _film_handler(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        url = str(request.url)
        if "search=" in url:
            return httpx.Response(200, text=_text("search_film.html"))
        if "archipel-pilote-2021-hd-vff" in url:
            return httpx.Response(200, text=_text("film_hd_vff.html"))
        return httpx.Response(404, text="absent")

    return handler


def _seed_film(client, *, title: str = "Archipel Pilote") -> int:
    created = client.post(
        "/api/v1/media",
        json={
            "type": "film",
            "title": title,
            "year": 2021,
            "new_episode_policy": "auto",
            "next_air_date": "2026-10-10",
            "pref_quality": "1080p",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _binding(
    db,
    media_id: int,
    provider_id: int,
    *,
    path: str,
    url: str,
    scanned_at: datetime | None = None,
    last_error: str | None = None,
) -> SourceBinding:
    moment = scanned_at or NOW
    row = SourceBinding(
        media_id=media_id,
        provider_id=provider_id,
        external_ref=url[:300],
        url=url,
        path=path,
        quality="1080p",
        language="VFF",
        season=None,
        active=True,
        last_scanned_at=moment,
        last_error=last_error,
        last_error_at=NOW if last_error else None,
    )
    db.add(row)
    db.commit()
    return row


def test_path_helpers():
    assert path_from_url("https://exemple.test/film/fictif-hd-vff") == "/film/fictif-hd-vff"
    assert url_from_path(NEW_BASE, "/film/fictif-hd-vff") == f"{NEW_BASE}/film/fictif-hd-vff"


def test_two_polls_on_linked_film_issue_zero_search(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    media_id = _seed_film(client)
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(
            db,
            media_id,
            provider.id,
            path="/film/archipel-pilote-2021-hd-vff",
            url=f"{BASE}/film/archipel-pilote-2021-hd-vff",
        )
    finally:
        db.close()

    calls: list[str] = []
    first = _poll(app, media_id, _film_handler(calls))
    assert first.result == "found"
    searches_first = sum(1 for url in calls if _is_search(url))
    assert searches_first == 0

    calls.clear()
    second = _poll(app, media_id, _film_handler(calls))
    assert second.result == "found"
    assert sum(1 for url in calls if _is_search(url)) == 0


def test_error_binding_triggers_one_search(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    media_id = _seed_film(client, title="Archipel Pilote")
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(
            db,
            media_id,
            provider.id,
            path="/film/archipel-pilote-2021-hd-vff",
            url=f"{BASE}/film/archipel-pilote-2021-hd-vff",
            last_error="Page introuvable",
        )
    finally:
        db.close()

    calls: list[str] = []
    report = _poll(app, media_id, _film_handler(calls))
    assert report.result == "found"
    assert sum(1 for url in calls if _is_search(url)) == 1


def test_domain_change_recomposes_poll_url(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": NEW_BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    media_id = _seed_film(client, title="Atlas Fictif")
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(
            db,
            media_id,
            provider.id,
            path="/film/archipel-pilote-2021-hd-vff",
            url=f"{OLD_BASE}/film/archipel-pilote-2021-hd-vff",
        )
    finally:
        db.close()

    calls: list[str] = []
    _poll(app, media_id, _film_handler(calls))
    page_hits = [url for url in calls if "archipel-pilote-2021-hd-vff" in url and not _is_search(url)]
    assert page_hits
    assert all(url.startswith(NEW_BASE) for url in page_hits)
    assert not any(OLD_BASE in url for url in page_hits)


def test_migration_backfills_path_idempotently(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from mediatracker.db.migrate import make_config

    url = f"{OLD_BASE}/film/archive-fictive-hd-vff"
    db_path = tmp_path / "migrate.db"
    db_url = f"sqlite:///{db_path}"
    command.upgrade(make_config(db_url), "0026_remote_search_at")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO provider (
                    id, kind, type, name, enabled, priority, config, builtin, created_at
                ) VALUES (
                    1, 'source', 'wawacity', 'Wawacity', 1, 0, '{}', 1, '2020-01-01 00:00:00'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO media (
                    id, type, title, year, followed, pref_quality, pref_language,
                    allow_fallback, auto_delete, keep, on_hold, new_episode_policy, created_at
                ) VALUES (
                    1, 'film', 'Fictif', 2020, 0, 'HD', 'VFF', 0, 0, 0, 0, 'propose', '2020-01-01 00:00:00'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO source_binding (
                    id, media_id, provider_id, external_ref, url, quality, language, active
                ) VALUES (
                    1, 1, 1, :ref, :page_url, 'HD', 'VFF', 1
                )
                """
            ),
            {"ref": url[:300], "page_url": url},
        )
    engine.dispose()

    command.upgrade(make_config(db_url), "0027_source_binding_path")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        path, stored_url = conn.execute(
            text("SELECT path, url FROM source_binding WHERE id = 1")
        ).one()
        assert path == "/film/archive-fictive-hd-vff"
        assert stored_url == url
    engine.dispose()

    command.upgrade(make_config(db_url), "head")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        path_again = conn.execute(text("SELECT path FROM source_binding WHERE id = 1")).scalar_one()
        assert path_again == path
    engine.dispose()


def test_stale_binding_runs_search_once(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    media_id = _seed_film(client, title="Archipel Pilote")
    stale = NOW - timedelta(days=31)
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(
            db,
            media_id,
            provider.id,
            path="/film/archipel-pilote-2021-hd-vff",
            url=f"{BASE}/film/archipel-pilote-2021-hd-vff",
            scanned_at=stale,
        )
    finally:
        db.close()

    calls: list[str] = []
    _poll(app, media_id, _film_handler(calls))
    assert sum(1 for url in calls if _is_search(url)) == 1
