"""Budget SQL pour GET /api/v1/library/folders (résolution media par lot)."""

from pathlib import Path

from sqlalchemy import event
from tests.conftest import login

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import Media


def _count_queries(engine):
    queries: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return queries, before


def _media_selects(queries: list[str]) -> int:
    def is_media(statement: str) -> bool:
        text = statement.lstrip()
        return " FROM media" in statement or text.startswith("SELECT media.")

    return sum(1 for statement in queries if is_media(statement))


def _seed_fictional_folders(series_root: Path, count: int) -> None:
    series_root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        folder = series_root / f"Fiction Brume {index:03d} (2024)"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"fiction-brume-{index:03d}.mkv").write_bytes(b"v")


def _seed_matching_media(db, count: int) -> None:
    for index in range(count):
        media = Media(
            type="series",
            title=f"Fiction Brume {index:03d}",
            year=2024,
            tmdb_id=700_000 + index,
            followed=index % 2 == 0,
            new_episode_policy="auto",
        )
        db.add(media)
    db.commit()


def test_library_folders_media_query_budget_is_flat(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        _seed_matching_media(db, 12)
    finally:
        db.close()

    series_root = layout.series_dir
    _seed_fictional_folders(series_root, 5)

    queries_small, hook = _count_queries(app.state.engine)
    try:
        small = client.get("/api/v1/library/folders")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert small.status_code == 200, small.text
    assert len(small.json()["items"]) == 5
    assert _media_selects(queries_small) == 1

    _seed_fictional_folders(series_root, 50)

    queries_large, hook = _count_queries(app.state.engine)
    try:
        large = client.get("/api/v1/library/folders")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert large.status_code == 200, large.text
    assert len(large.json()["items"]) == 50
    assert _media_selects(queries_large) == 1
    assert abs(len(queries_large) - len(queries_small)) <= 2

    sample = next(item for item in large.json()["items"] if item["title"] == "Fiction Brume 007")
    assert sample["media_id"] is not None
    assert sample["followed"] is False
    assert sample["tmdb_id"] == 700_007
