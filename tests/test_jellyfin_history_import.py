"""API import of Jellyfin history CSV."""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO

from tests.conftest import csrf, login

from mediatracker.db.models import Event, Media, MediaItem
from mediatracker.integrations.jellyfin_history_import import CSV_COLUMNS, FOLLOW_ORIGIN

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
TMDB_FILM = 880001
TMDB_SERIES = 880002


def _csv_row(**kwargs: str) -> str:
    base = {
        "type": "movie",
        "title": "Film Démo",
        "year": "2020",
        "series_title": "",
        "season": "",
        "episode": "",
        "tmdb_id": str(TMDB_FILM),
        "imdb_id": "",
        "tvdb_id": "",
        "jellyfin_item_id": "jf-film-1",
        "watched_at": "2024-05-01T10:00:00Z",
        "play_count": "1",
        "source": "userdata",
    }
    base.update(kwargs)
    header = ",".join(CSV_COLUMNS)
    line = ",".join(base[col] for col in CSV_COLUMNS)
    return f"{header}\n{line}\n"


def _auth(client):
    login(client)
    return csrf(client)


def _existing_film(db):
    media = Media(
        type="film",
        title="Film Démo",
        year=2020,
        tmdb_id=TMDB_FILM,
        followed=False,
        created_at=NOW,
    )
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, title=media.title, state="missing")
    db.add(item)
    db.commit()
    return media, item


def test_dry_run_does_not_mark_watched(client, app):
    db = app.state.session_factory()
    try:
        media, item = _existing_film(db)
        item_id = item.id
    finally:
        db.close()
    headers = _auth(client)
    files = {"upload": ("hist.csv", BytesIO(_csv_row().encode()), "text/csv")}
    preview = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=true&sync=true",
        headers=headers,
        files=files,
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["matched_count"] == 1
    db = app.state.session_factory()
    try:

        row = db.get(MediaItem, item_id)
        assert row is not None
        assert row.watched is False
    finally:
        db.close()


def test_confirm_marks_watched_and_is_idempotent(client, app):
    db = app.state.session_factory()
    try:
        _existing_film(db)
    finally:
        db.close()
    headers = _auth(client)
    payload = _csv_row().encode()
    first = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(payload), "text/csv")},
    )
    assert first.status_code == 200
    assert first.json()["applied"] == 1
    db = app.state.session_factory()
    try:
        from sqlalchemy import select

        item = db.scalar(select(MediaItem))
        assert item is not None
        assert item.watched is True
        assert item.watched_at is not None
        assert item.jellyfin_item_id == "jf-film-1"
        events = list(db.scalars(select(Event).where(Event.type == "jellyfin_history.imported")))
        assert len(events) == 1
    finally:
        db.close()
    second = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(payload), "text/csv")},
    )
    assert second.json()["applied"] == 0
    assert second.json()["already_ok"] == 1
    db = app.state.session_factory()
    try:
        from sqlalchemy import func, select

        assert db.scalar(select(func.count()).select_from(Event).where(Event.type == "jellyfin_history.imported")) == 1
    finally:
        db.close()


def test_creates_unfollowed_fiche(client, app):
    headers = _auth(client)
    csv_text = _csv_row(
        tmdb_id="",
        type="episode",
        title="Épisode démo",
        series_title="Série Démo",
        year="2021",
        season="1",
        episode="1",
        jellyfin_item_id="jf-ep-1",
    )
    result = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(csv_text.encode()), "text/csv")},
    )
    assert result.status_code == 200
    db = app.state.session_factory()
    try:
        from sqlalchemy import select

        media = db.scalar(select(Media).where(Media.title == "Série Démo"))
        assert media is not None
        assert media.followed is False
        assert media.follow_origin == FOLLOW_ORIGIN
    finally:
        db.close()


def test_unmatched_reported(client, app):
    headers = _auth(client)
    csv_text = _csv_row(
        tmdb_id="",
        imdb_id="",
        type="episode",
        title="Épisode orphelin",
        series_title="",
        season="",
        episode="",
        jellyfin_item_id="",
    )
    preview = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=true&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(csv_text.encode()), "text/csv")},
    )
    assert preview.json()["unmatched_count"] == 1


def test_series_vu_status(client, app):
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Série Démo", year=2021, tmdb_id=TMDB_SERIES, created_at=NOW)
        db.add(media)
        db.flush()
        for ep in (1, 2):
            db.add(
                MediaItem(
                    media_id=media.id,
                    season=1,
                    episode=ep,
                    air_date=date(2021, 1, ep),
                    state="missing",
                )
            )
        db.commit()
    finally:
        db.close()
    headers = _auth(client)
    header = ",".join(CSV_COLUMNS)
    row_tpl = "episode,Épisode {ep},2021,Série Démo,1,{ep},{tmdb},,,jf-{ep},2024-05-0{ep}T10:00:00Z,1,userdata"
    csv_text = header + "\n" + "\n".join(row_tpl.format(ep=ep, tmdb=TMDB_SERIES) for ep in (1, 2)) + "\n"
    client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(csv_text.encode()), "text/csv")},
    )
    from mediatracker.api.v1.media import _media_watched

    db = app.state.session_factory()
    try:
        from sqlalchemy import select

        media = db.scalar(select(Media).where(Media.tmdb_id == TMDB_SERIES))
        episodes = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)))
        assert _media_watched(media, episodes)
    finally:
        db.close()


def test_refollow_selected(client, app):
    headers = _auth(client)
    db = app.state.session_factory()
    try:
        _existing_film(db)
    finally:
        db.close()
    payload = _csv_row().encode()
    imported = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(payload), "text/csv")},
    )
    assert imported.status_code == 200
    media_id = imported.json()["follow_candidates"][0]["id"]
    listed = client.get("/api/v1/library/jellyfin-history/follow-candidates", headers=headers)
    assert listed.json()["items"][0]["id"] == media_id
    refollow = client.post(
        "/api/v1/library/jellyfin-history/refollow",
        headers=headers,
        json={"media_ids": [media_id]},
    )
    assert refollow.json()["updated"] == 1
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row.followed is True
    finally:
        db.close()


def test_refollow_batch_activation(client, app):
    db = app.state.session_factory()
    try:
        for idx, tmdb in enumerate((880101, 880102), start=1):
            media = Media(
                type="film",
                title=f"Film Démo {idx}",
                year=2020,
                tmdb_id=tmdb,
                followed=False,
                created_at=NOW,
            )
            db.add(media)
            db.flush()
            db.add(MediaItem(media_id=media.id, title=media.title, state="missing"))
        db.commit()
    finally:
        db.close()
    headers = _auth(client)
    header = ",".join(CSV_COLUMNS)
    rows = [
        _csv_row(title="Film Démo 1", tmdb_id="880101", jellyfin_item_id="jf-a").strip().split("\n", 1)[1],
        _csv_row(title="Film Démo 2", tmdb_id="880102", jellyfin_item_id="jf-b").strip().split("\n", 1)[1],
    ]
    csv_text = header + "\n" + "\n".join(rows) + "\n"
    imported = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(csv_text.encode()), "text/csv")},
    )
    assert imported.status_code == 200
    ids = [row["id"] for row in imported.json()["follow_candidates"]]
    assert len(ids) == 2
    refollow = client.post(
        "/api/v1/library/jellyfin-history/refollow",
        headers=headers,
        json={"media_ids": ids},
    )
    assert refollow.json()["updated"] == 2
    db = app.state.session_factory()
    try:
        from sqlalchemy import select

        rows_db = list(db.scalars(select(Media).where(Media.id.in_(ids))))
        assert all(row.followed for row in rows_db)
    finally:
        db.close()


def test_import_preserves_already_followed_fiche(client, app):
    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Film Démo",
            year=2020,
            tmdb_id=TMDB_FILM,
            followed=True,
            follow_origin="user",
            created_at=NOW,
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, title=media.title, state="missing"))
        db.commit()
        media_id = media.id
    finally:
        db.close()
    headers = _auth(client)
    response = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(_csv_row().encode()), "text/csv")},
    )
    assert response.status_code == 200
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row is not None
        assert row.followed is True
        assert row.follow_origin == "user"
    finally:
        db.close()


def test_import_confirms_one_tmdb_fetch_per_series(client, app, monkeypatch):
    from mediatracker.integrations.tmdb import TmdbTitle

    fetch_calls: list[tuple[int, str]] = []

    def fake_maybe_fetch(
        db, settings, secret_key, tmdb_id, media_type, *, tmdb_cache=None, import_tx=None, **kwargs
    ):
        del import_tx
        del db, settings, secret_key
        key = (media_type, tmdb_id)
        if tmdb_cache is not None and key in tmdb_cache:
            return tmdb_cache[key]
        fetch_calls.append((tmdb_id, media_type))
        title = TmdbTitle(tmdb_id, media_type, "Série Démo", 2021, None, None, None, None, ())
        if tmdb_cache is not None:
            tmdb_cache[key] = title
        return title

    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_history_import._maybe_fetch",
        fake_maybe_fetch,
    )
    headers = _auth(client)
    header = ",".join(CSV_COLUMNS)
    row_tpl = "episode,Épisode {ep},2021,Série Démo,1,{ep},{tmdb},,,jf-{ep},2024-05-0{ep}T10:00:00Z,1,userdata"
    csv_text = header + "\n" + "\n".join(row_tpl.format(ep=ep, tmdb=TMDB_SERIES) for ep in (1, 2, 3)) + "\n"
    result = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=false&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(csv_text.encode()), "text/csv")},
    )
    assert result.status_code == 200
    assert fetch_calls == [(TMDB_SERIES, "series")]
