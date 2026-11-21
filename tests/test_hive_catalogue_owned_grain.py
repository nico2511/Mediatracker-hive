"""Catalogue Hive : hide_owned au grain épisode, in_library = fichier présent."""

from __future__ import annotations

from tests.conftest import login, write_sparse_video_file
from tests.test_hive_t6 import _enable, _seed_catalogue_row

pytest_plugins = ["tests.test_hive_t6"]


def test_catalogue_hide_owned_keeps_series_with_missing_episode(client, tmp_path):
    login(client)
    _enable(client)
    db = client.app.state.session_factory()
    tmdb_id = 77201
    try:
        from mediatracker.db.models import Media, MediaItem

        for ep in ((1, 1), (1, 2)):
            cle = {
                "type": "series",
                "tmdb_id": tmdb_id,
                "saison": ep[0],
                "episode": ep[1],
                "version": "HD+VFF",
            }
            sha = f"{ep[0]}{ep[1]}" + "a" * 62
            _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha=sha, joignable=True, taille=0)
        media = Media(type="series", title="Série fictive", year=2099, tmdb_id=tmdb_id, followed=True)
        db.add(media)
        db.flush()
        fpath = tmp_path / "s01e01.mkv"
        write_sparse_video_file(fpath, 128_000)
        db.add(
            MediaItem(
                media_id=media.id,
                season=1,
                episode=1,
                downloaded=True,
                file_path=str(fpath),
                state="downloaded",
            )
        )
        db.add(MediaItem(media_id=media.id, season=1, episode=2, state="missing"))
        db.commit()
    finally:
        db.close()

    page = client.get("/api/v1/hive/catalogue", params={"hide_owned": "true", "media_type": "series"}).json()
    tmdb_hits = [w for w in page["items"] if w.get("tmdb_id") == tmdb_id]
    assert len(tmdb_hits) == 1


def test_catalogue_in_library_requires_present_file(client, tmp_path):
    login(client)
    _enable(client)
    db = client.app.state.session_factory()
    cle = {"type": "film", "tmdb_id": 77202, "version": "HD+VFF"}
    try:
        from mediatracker.db.models import Media, MediaItem

        _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha="f" * 64, joignable=True, taille=0)
        media = Media(type="film", title="Film fictif", year=2099, tmdb_id=77202, followed=True)
        db.add(media)
        db.flush()
        fpath = tmp_path / "owned.mkv"
        write_sparse_video_file(fpath, 128_000)
        db.add(
            MediaItem(media_id=media.id, downloaded=True, file_path=str(fpath), state="downloaded")
        )
        db.commit()
        from mediatracker.hive.hive_catalogue import _stable_work_key

        wk = _stable_work_key(cle)
    finally:
        db.close()

    row = client.get(f"/api/v1/hive/catalogue/{wk}").json()
    assert row["in_library"] is True


def test_catalogue_in_library_false_when_trashed(client, settings):
    login(client)
    _enable(client)
    from pathlib import Path

    from tests.conftest import PLAUSIBLE_VIDEO_BYTES

    from mediatracker.core.cleanup import delete_files

    movies = Path(settings.data_dir) / "media" / "Films"
    fpath = movies / "CorbeilleGrain" / "Fictif.mkv"
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_bytes(b"x" * PLAUSIBLE_VIDEO_BYTES)
    cle = {"type": "film", "tmdb_id": 77203, "version": "HD+VFF"}
    db = client.app.state.session_factory()
    try:
        from mediatracker.db.models import Media, MediaItem
        from mediatracker.hive.hive_catalogue import _stable_work_key

        _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha="c" * 64, joignable=True, taille=0)
        media = Media(type="film", title="Film corbeille", year=2099, tmdb_id=77203, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(fpath), state="downloaded")
        db.add(item)
        db.commit()
        media_id, item_id = media.id, item.id
        wk = _stable_work_key(cle)
    finally:
        db.close()

    assert client.get(f"/api/v1/hive/catalogue/{wk}").json()["in_library"] is True
    db = client.app.state.session_factory()
    try:
        from datetime import datetime, timezone

        delete_files(
            db,
            settings,
            media_id=media_id,
            scope="episode",
            season=None,
            item_id=item_id,
            now=datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
        )
        db.commit()
    finally:
        db.close()

    row = client.get(f"/api/v1/hive/catalogue/{wk}").json()
    assert row["in_library"] is False
    hidden = client.get("/api/v1/hive/catalogue", params={"hide_owned": "true", "media_type": "film"}).json()
    assert any(w.get("tmdb_id") == 77203 for w in hidden["items"])
