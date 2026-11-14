"""Budget SQL pour GET /api/v1/catalog (correctif audit P7 n°9).

Référence mesurée sur ``main`` avant correctif #9, seed 500 entrées « Fiction Brume »
(films, qualité/langue par défaut) :

| Métrique | Avant (main) | Après (SQL LIMIT + COUNT) |
|----------|--------------|---------------------------|
| ``SELECT`` catalogue (lignes ORM) | **500** | **20** (``limit=20``) |
| Requêtes ``catalog_entry`` (page) | 1 sans ``LIMIT`` | 2 (``COUNT`` + ``LIMIT``) |
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event
from tests.conftest import connect_alldebrid_for_tests, login

from mediatracker.db.models import CatalogEntry

NOW = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _catalog_sources_usable(app, client):
    del client
    connect_alldebrid_for_tests(app)


def _count_queries(engine):
    queries: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return queries, before


def _catalog_entry_selects(queries: list[str]) -> list[str]:
    return [statement for statement in queries if "catalog_entry" in statement.lower()]


def _has_limit(statement: str) -> bool:
    return " limit " in statement.lower()


def _seed_catalog_entries(db, count: int, *, start: int = 0) -> None:
    for index in range(start, start + count):
        moment = NOW - timedelta(hours=index)
        entry = CatalogEntry(
            dedupe_key=f"fiction-brume-{index:03d}|2024|film||",
            title=f"Fiction Brume {index:03d}",
            year=2024,
            media_type="film",
            quality="HD",
            language="VFF",
            source="wawacity",
            source_url=f"https://catalogue-fictif.example/film-{index:03d}",
            sources=[],
            first_seen_at=moment,
            last_seen_at=moment,
            genres=["Aventure"] if index % 5 == 0 else [],
        )
        db.add(entry)
    db.commit()


def test_catalog_list_row_budget_is_bounded(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        _seed_catalog_entries(db, 500)
    finally:
        db.close()

    queries, hook = _count_queries(app.state.engine)
    try:
        response = client.get("/api/v1/catalog", params={"type": "film", "limit": 20})
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 500
    assert len(body["items"]) == 20
    assert body["items"][0]["title"] == "Fiction Brume 000"

    catalog_sql = _catalog_entry_selects(queries)
    assert len(catalog_sql) >= 2
    limited = [statement for statement in catalog_sql if _has_limit(statement)]
    assert len(limited) == 1
    assert "count" in catalog_sql[0].lower() or "count" in catalog_sql[1].lower()


def test_catalog_list_query_count_stable(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        _seed_catalog_entries(db, 50)
    finally:
        db.close()

    queries_small, hook = _count_queries(app.state.engine)
    try:
        small = client.get("/api/v1/catalog", params={"type": "film", "limit": 20})
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert small.status_code == 200

    db = app.state.session_factory()
    try:
        _seed_catalog_entries(db, 450, start=50)
    finally:
        db.close()

    queries_large, hook = _count_queries(app.state.engine)
    try:
        large = client.get("/api/v1/catalog", params={"type": "film", "limit": 20})
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert large.status_code == 200
    assert large.json()["total"] == 500
    assert len(large.json()["items"]) == 20

    small_catalog = _catalog_entry_selects(queries_small)
    large_catalog = _catalog_entry_selects(queries_large)
    assert abs(len(large_catalog) - len(small_catalog)) <= 1
