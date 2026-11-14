"""API file_status: present, trash, absent, downloading, season aggregation, query budget."""

from datetime import date, datetime, timezone

from sqlalchemy import event
from tests.conftest import csrf, login

from mediatracker.core.forget import TRACE_DELETED_VOLUNTARILY, TRACE_FILE_ABSENT
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem, TrashEntry

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def _series(db, title: str = "Brume Port") -> Media:
    media = Media(type="series", title=title, year=2024, tmdb_id=9100, followed=True, new_episode_policy="auto")
    db.add(media)
    db.flush()
    return media


def _episode(db, media: Media, season: int, episode: int, **overrides) -> MediaItem:
    values = dict(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"Épisode {episode}",
        air_date=date(2025, 6, episode),
        state="missing",
        downloaded=False,
        watched=False,
    )
    values.update(overrides)
    item = MediaItem(**values)
    db.add(item)
    db.flush()
    return item


def _count_queries(engine):
    queries: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return queries, before


def test_four_file_statuses_on_episodes(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Quatre États")
        present = _episode(db, media, 1, 1)
        trash = _episode(db, media, 1, 2)
        _episode(db, media, 1, 3, traces=[{"kind": TRACE_FILE_ABSENT, "at": NOW.isoformat()}])
        loading = _episode(db, media, 1, 4)
        db.add(
            LibraryFile(
                path="/fiction/Quatre.Etats.S01E01.mkv",
                parsed_title="Quatre États",
                media_type="series",
                media_id=media.id,
                media_item_id=present.id,
                season=1,
                episode=1,
                status="matched",
            )
        )
        db.add(
            TrashEntry(
                original_path="/fiction/Quatre.Etats.S01E02.mkv",
                trash_path="/trash/Quatre.Etats.S01E02.mkv",
                media_id=media.id,
                media_item_id=trash.id,
                season=1,
                label="S01E02",
                deleted_at=NOW,
                purge_after=NOW,
            )
        )
        db.add(Download(media_item_id=loading.id, state="downloading", filename="S01E04.mkv", created_at=NOW))
        db.commit()
        media_id = media.id
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}/items")
    assert response.status_code == 200, response.text
    episodes = {row["episode"]: row for row in response.json()["seasons"][0]["episodes"]}
    assert episodes[1]["file_status"] == "present"
    assert episodes[1]["file_absent"] is False
    assert episodes[2]["file_status"] == "trash"
    assert episodes[2]["trash_entry_id"] is not None
    assert episodes[3]["file_status"] == "absent"
    assert episodes[3]["file_absent"] is True
    assert episodes[4]["file_status"] == "downloading"


def test_voluntary_trace_counts_as_trash(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Volontaire Lac")
        _episode(
            db,
            media,
            1,
            1,
            traces=[{"kind": TRACE_DELETED_VOLUNTARILY, "at": NOW.isoformat()}],
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}/items")
    assert response.status_code == 200
    episode = response.json()["seasons"][0]["episodes"][0]
    assert episode["file_status"] == "trash"


def test_season_aggregation_counts(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Agrégat Rivage")
        e1 = _episode(db, media, 2, 1)
        e2 = _episode(db, media, 2, 2)
        _episode(db, media, 2, 3)
        db.add(
            LibraryFile(
                path="/fiction/Aggregat.Rivage.S02E01.mkv",
                parsed_title="Agrégat Rivage",
                media_type="series",
                media_id=media.id,
                media_item_id=e1.id,
                season=2,
                episode=1,
                status="matched",
            )
        )
        db.add(
            LibraryFile(
                path="/fiction/Aggregat.Rivage.S02E02.mkv",
                parsed_title="Agrégat Rivage",
                media_type="series",
                media_id=media.id,
                media_item_id=e2.id,
                season=2,
                episode=2,
                status="trashed",
            )
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}/items")
    assert response.status_code == 200
    season = next(s for s in response.json()["seasons"] if s["number"] == 2)
    assert season["file_status_counts"] == {"present": 1, "trash": 1, "absent": 1, "downloading": 0}
    assert season["file_status"] == "present"


def test_film_file_status_on_media_detail(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Film Zéphyr", year=2023, tmdb_id=9200, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=None, episode=None, state="downloaded", downloaded=True)
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path="/fiction/Film.Zephyr.2023.mkv",
                parsed_title="Film Zéphyr",
                media_type="film",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
            )
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}")
    assert response.status_code == 200
    assert response.json()["file_status"] == "present"


def test_list_items_query_budget_for_fifty_episodes(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Cinquantaine", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    db = app.state.session_factory()
    try:
        for episode in range(1, 51):
            db.add(
                MediaItem(
                    media_id=media_id,
                    season=1,
                    episode=episode,
                    title=f"Slot {episode}",
                    air_date=date(2020, 1, min(episode, 28)),
                    state="missing",
                )
            )
        db.commit()
    finally:
        db.close()

    queries, hook = _count_queries(app.state.engine)
    try:
        response = client.get(f"/api/v1/media/{media_id}/items")
    finally:
        event.remove(app.state.engine, "before_cursor_execute", hook)
    assert response.status_code == 200, response.text
    assert len(response.json()["seasons"][0]["episodes"]) == 50
    assert len(queries) <= 18
