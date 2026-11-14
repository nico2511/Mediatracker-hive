"""Changement d'identifiant TMDB : confirmation et épisodes (série, données fictives)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.tmdb.id_replace import REASSOCIATE_NOTE_PREFIX, library_file_awaiting_reassociate
from mediatracker.db.models import LibraryFile, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbSeason, TmdbTitle
from mediatracker.library.scanner import _ingest


def _series_title(tmdb_id: int, *episodes: tuple[int, int, int]) -> TmdbTitle:
    by_season: dict[int, list[TmdbEpisode]] = {}
    for season, episode, ep_tmdb in episodes:
        by_season.setdefault(season, []).append(
            TmdbEpisode(
                season=season,
                episode=episode,
                title=f"S{season}E{episode}",
                air_date=date(2020, 1, 1),
                tmdb_episode_id=ep_tmdb,
            )
        )
    seasons = tuple(
        TmdbSeason(
            number=num,
            name=f"Saison {num}",
            overview=None,
            air_date=None,
            episode_count=len(eps),
            tmdb_id=10_000 + num,
            episodes=tuple(eps),
        )
        for num, eps in sorted(by_season.items())
    )
    return TmdbTitle(
        tmdb_id=tmdb_id,
        media_type="series",
        title=f"Série {tmdb_id}",
        year=2020,
        overview=None,
        poster_url=None,
        series_status="En cours",
        release_date=None,
        seasons=seasons,
    )


def _seed_wrong_series(client, app, wrong_id: int = 100, good_id: int = 200):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Serie TMDB", followed=False, tmdb_id=wrong_id, year=2020)
        db.add(media)
        db.flush()
        wrong = _series_title(wrong_id, (1, 1, 1001), (1, 2, 1002), (2, 9, 1099))
        for season in wrong.seasons:
            for ep in season.episodes:
                db.add(
                    MediaItem(
                        media_id=media.id,
                        season=ep.season,
                        episode=ep.episode,
                        title=ep.title,
                        tmdb_episode_id=ep.tmdb_episode_id,
                        state="missing",
                    )
                )
        db.commit()
        return media.id
    finally:
        db.close()


def test_tmdb_id_change_requires_confirm_and_rolls_back(client, app, monkeypatch):
    mid = _seed_wrong_series(client, app)
    good = _series_title(200, (1, 1, 2001), (1, 2, 2002))

    def _fetch(db, secret, tmdb_id, media_type):
        assert tmdb_id == 200
        return good, None

    monkeypatch.setattr("mediatracker.api.v1.media._fetch_tmdb_title", _fetch)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "token")

    blocked = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 200}, headers=csrf(client))
    assert blocked.status_code == 409, blocked.text
    assert blocked.json().get("needs_confirm") is True
    change = blocked.json().get("tmdb_id_change", {})
    assert change["new_tmdb_id"] == 200
    assert any(row["season"] == 2 and row["episode"] == 9 for row in change["removed_if_rebuild"])
    assert any(row["has_file"] for row in change.get("files_to_reassociate", [])) is False

    db = app.state.session_factory()
    try:
        assert db.get(Media, mid).tmdb_id == 100
        assert db.query(MediaItem).filter(MediaItem.media_id == mid).count() == 3
    finally:
        db.close()


def test_tmdb_id_change_rebuild_removes_rows_keeps_file_reassociate(client, app, monkeypatch, tmp_path):
    mid = _seed_wrong_series(client, app)
    good = _series_title(200, (1, 1, 2001), (1, 2, 2002))

    monkeypatch.setattr(
        "mediatracker.api.v1.media._fetch_tmdb_title",
        lambda db, secret, tmdb_id, media_type: (good, None),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "token")

    file_path = tmp_path / "s02e09.mkv"
    file_path.write_bytes(b"video-bytes")
    overlap_path = tmp_path / "s01e01.mkv"
    overlap_path.write_bytes(b"overlap")

    db = app.state.session_factory()
    try:
        orphan = db.scalars(select(MediaItem).where(MediaItem.media_id == mid, MediaItem.season == 2)).one()
        orphan.downloaded = True
        orphan.file_path = str(file_path)
        overlap = db.scalars(
            select(MediaItem).where(
                MediaItem.media_id == mid,
                MediaItem.season == 1,
                MediaItem.episode == 1,
            )
        ).one()
        overlap.downloaded = True
        overlap.file_path = str(overlap_path)
        db.commit()
    finally:
        db.close()

    preview = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 200}, headers=csrf(client))
    reassoc = preview.json()["tmdb_id_change"]["files_to_reassociate"]
    assert any(r["season"] == 2 and r["episode"] == 9 for r in reassoc)
    assert any(r["season"] == 1 and r["episode"] == 1 for r in reassoc)

    ok = client.patch(
        f"/api/v1/media/{mid}",
        json={"tmdb_id": 200, "tmdb_id_confirm": "rebuild_episodes"},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text
    assert file_path.is_file()

    db = app.state.session_factory()
    try:
        slots = {
            (r.season, r.episode)
            for r in db.scalars(select(MediaItem).where(MediaItem.media_id == mid)).all()
        }
        assert (2, 9) not in slots
        assert (1, 1) in slots
        s11 = db.scalars(
            select(MediaItem).where(MediaItem.media_id == mid, MediaItem.season == 1, MediaItem.episode == 1)
        ).one()
        assert s11.tmdb_episode_id == 2001
        assert s11.file_path is None
        assert s11.downloaded is False

        lib = db.scalars(select(LibraryFile).where(LibraryFile.path == str(file_path.resolve()))).one()
        assert library_file_awaiting_reassociate(lib)
        assert REASSOCIATE_NOTE_PREFIX in (lib.note or "")
        assert lib.media_id is None and lib.media_item_id is None

        from mediatracker.library.parse import parse_path

        parsed = parse_path(file_path, root=tmp_path, root_kind="series")
        status, _ = _ingest(db, file_path, parsed, datetime.now(timezone.utc), tmdb=None)
        db.commit()
        assert status == "review"
        lib2 = db.scalars(select(LibraryFile).where(LibraryFile.path == str(file_path.resolve()))).one()
        assert lib2.media_id is None
    finally:
        db.close()


def test_tmdb_id_change_without_token_no_episode_preview(client, app, monkeypatch):
    mid = _seed_wrong_series(client, app)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")

    blocked = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 200}, headers=csrf(client))
    assert blocked.status_code == 409, blocked.text
    body = blocked.json()
    assert body.get("needs_confirm") is True
    assert body.get("tmdb_preview_available") is False
    assert body.get("previous_tmdb_id") == 100
    assert body.get("new_tmdb_id") == 200
    assert "removed_if_rebuild" not in body
    assert "tmdb_id_change" not in body

    rebuild = client.patch(
        f"/api/v1/media/{mid}",
        json={"tmdb_id": 200, "tmdb_id_confirm": "rebuild_episodes"},
        headers=csrf(client),
    )
    assert rebuild.status_code == 422, rebuild.text


def test_first_tmdb_id_still_direct(client, app, monkeypatch):
    login(client)
    db = app.state.session_factory()
    media = Media(type="series", title="Neuve", followed=False)
    db.add(media)
    db.commit()
    mid = media.id
    db.close()

    title = _series_title(300, (1, 1, 3001))
    monkeypatch.setattr(
        "mediatracker.api.v1.media._fetch_tmdb_title",
        lambda db, secret, tmdb_id, media_type: (title, None),
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "token")

    r = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 300}, headers=csrf(client))
    assert r.status_code == 200, r.text
    assert r.json()["tmdb_id"] == 300
