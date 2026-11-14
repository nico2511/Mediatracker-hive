"""Budget SQL pour ``next_due`` (correctif audit P7 n°8).

Référence mesurée sur ``main`` avant correctif #8, seed « Série Brume » (N séries
suivies sans épisode, toutes éligibles au gate ``no_air_date``) :

| Métrique | Avant (main) | Après (keyset par lots) |
|----------|--------------|-------------------------|
| ``SELECT … FROM media`` (candidats) | 1 requête, **N lignes** | 1 requête / lot (≤ 50), pagination **keyset** |
| ``SELECT … FROM media_item`` | 1 requête (``IN`` tous les ids) | 1 requête par lot (``_items_for``) |
"""

from datetime import datetime, timezone

from sqlalchemy import event
from tests.conftest import csrf, login

from mediatracker.core.source_poll import next_due
from mediatracker.db.models import Media


def _count_queries(engine):
    queries: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return queries, before


def _media_poll_selects(queries: list[str]) -> list[str]:
    picked: list[str] = []
    for statement in queries:
        text = statement.upper()
        if "FROM MEDIA" not in text:
            continue
        if " JOIN MEDIA_ITEM" in text:
            continue
        if "ORDER BY" not in text or "NEXT_SOURCE_POLL_AT" not in text:
            continue
        picked.append(statement)
    return picked


def _media_item_selects(queries: list[str]) -> list[str]:
    return [statement for statement in queries if "FROM MEDIA_ITEM" in statement.upper()]


def _has_keyset_cursor(statement: str) -> bool:
    return "coalesce" in statement.lower()


def _seed_due_series(db, count: int, *, stamp: datetime | None = None) -> None:
    moment = stamp or datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    for index in range(count):
        media = Media(
            type="series",
            title=f"Série Brume {index:03d}",
            year=2024,
            tmdb_id=800_000 + index,
            followed=True,
            new_episode_policy="auto",
            next_source_poll_at=moment if index % 3 == 0 else None,
            last_source_poll_at=None if index % 2 == 0 else moment,
        )
        db.add(media)
    db.commit()


def test_next_due_media_row_budget_is_flat(client, app):
    login(client)
    now = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        _seed_due_series(db, 5, stamp=now)
    finally:
        db.close()

    queries_small, hook = _count_queries(app.state.engine)
    db = app.state.session_factory()
    try:
        winner_small = next_due(db, app.state.settings, now)
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
        db.close()

    db = app.state.session_factory()
    try:
        _seed_due_series(db, 45, stamp=now)
    finally:
        db.close()

    queries_large, hook = _count_queries(app.state.engine)
    db = app.state.session_factory()
    try:
        winner_large = next_due(db, app.state.settings, now)
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
        db.close()

    poll_small = _media_poll_selects(queries_small)
    poll_large = _media_poll_selects(queries_large)
    assert len(poll_small) == 1
    assert len(poll_large) == 1
    assert abs(len(queries_large) - len(queries_small)) <= 3
    assert len(_media_item_selects(queries_large)) == 1
    assert winner_small is not None
    assert winner_large is not None


def test_next_due_tie_breaks_on_equal_schedule(client, app):
    login(client)
    stamp = datetime(2026, 3, 1, 8, tzinfo=timezone.utc)
    now = datetime(2026, 3, 2, 8, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        first = Media(
            type="series",
            title="Égalité A",
            year=2020,
            tmdb_id=801_001,
            followed=True,
            new_episode_policy="auto",
            next_source_poll_at=stamp,
            last_source_poll_at=stamp,
        )
        second = Media(
            type="series",
            title="Égalité B",
            year=2020,
            tmdb_id=801_002,
            followed=True,
            new_episode_policy="auto",
            next_source_poll_at=stamp,
            last_source_poll_at=stamp,
        )
        db.add(first)
        db.add(second)
        db.commit()
        db.refresh(first)
        db.refresh(second)
        low_id, high_id = sorted((first.id, second.id))
        chosen = next_due(db, app.state.settings, now)
        assert chosen is not None
        assert chosen.id == low_id
        assert chosen.id != high_id
    finally:
        db.close()


def _seed_finished_then_open(db, refused: int) -> Media:
    """``refused`` fiches refusées (série terminée), puis une seule autorisée."""
    for index in range(refused):
        db.add(
            Media(
                type="series",
                title=f"Terminée {index:03d}",
                year=2019,
                tmdb_id=810_000 + index,
                followed=True,
                new_episode_policy="auto",
                series_status="ended",
            )
        )
    allowed = Media(
        type="series",
        title="Encore active",
        year=2025,
        tmdb_id=810_999,
        followed=True,
        new_episode_policy="auto",
        series_status="returning",
    )
    db.add(allowed)
    db.commit()
    db.refresh(allowed)
    return allowed


def test_next_due_keyset_batch_survives_many_gate_refusals(client, app):
    """50 refus ``series_finished`` + 1 autorisée : pas de parcours OFFSET quadratique."""
    login(client)
    client.put(
        "/api/v1/settings",
        json={"values": {"scan_ongoing_only": True}},
        headers=csrf(client),
    )
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        allowed = _seed_finished_then_open(db, 50)
    finally:
        db.close()

    queries, hook = _count_queries(app.state.engine)
    db = app.state.session_factory()
    try:
        chosen = next_due(db, app.state.settings, now)
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
        db.close()

    assert chosen is not None
    assert chosen.id == allowed.id
    poll_batches = _media_poll_selects(queries)
    assert len(poll_batches) == 2
    assert not _has_keyset_cursor(poll_batches[0])
    assert _has_keyset_cursor(poll_batches[1])
    assert len(_media_item_selects(queries)) == 2
    # Parcours candidats (lots media + épisodes), pas le coût interne du gate.
    assert len(poll_batches) + len(_media_item_selects(queries)) <= 6
