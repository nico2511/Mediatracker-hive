"""Rapprochement clé film/movie et choix du MediaItem avec fichier présent."""

from __future__ import annotations

from tests.test_hive_cherche_version_dedup import _enable_hive_in_db
from tests.test_hive_t2 import _join_fixture_ruche, _origin_id

from mediatracker.db.models import Media, MediaItem
from mediatracker.hive.signal_requests import _find_local_item_for_cle


def test_find_local_item_movie_cle_film_media(client, settings, tmp_path):
    db = client.app.state.session_factory()
    try:
        _enable_hive_in_db(db, settings)
        _join_fixture_ruche(db, _origin_id(settings))
        media = Media(type="film", title="Fiche film", year=2099, tmdb_id=880500, followed=False)
        db.add(media)
        db.flush()
        lib = tmp_path / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        fpath = lib / "local.mkv"
        fpath.write_bytes(b"\x01" * 128)
        db.add(
            MediaItem(
                media_id=media.id,
                downloaded=True,
                file_path=str(fpath),
                release_quality="1080p",
                release_language="VFF",
            )
        )
        db.flush()
        cle = {"type": "movie", "tmdb_id": 880500, "version": "1080p+VFF"}
        matched = _find_local_item_for_cle(db, cle)
        assert matched is not None
        found_media, item = matched
        assert found_media.type == "film"
        assert item.file_path == str(fpath)
        db.commit()
    finally:
        db.close()


def test_find_local_item_film_ignores_marked_downloaded_without_file(client, settings, tmp_path):
    db = client.app.state.session_factory()
    try:
        _enable_hive_in_db(db, settings)
        _join_fixture_ruche(db, _origin_id(settings))
        media = Media(type="film", title="Sans fichier", year=2099, tmdb_id=880501, followed=False)
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, downloaded=True, state="downloaded"))
        db.flush()
        cle = {"type": "film", "tmdb_id": 880501, "version": "1080p+VFF"}
        assert _find_local_item_for_cle(db, cle) is None
        db.commit()
    finally:
        db.close()
