"""V4 : cohérence du statut fichier (Présent / Corbeille / Absent / En cours) sur les API exposées.

Bibliothèque factice (tmp_path), noms fictifs, SQLite temporaire, pas de réseau réel.
"""

from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from tests.conftest import csrf, login
from tests.test_integration_v1_v2 import NOW, _enable_cleanup, _episode, _film, _film_item, _video

from mediatracker.core.forget import TRACE_FILE_ABSENT, forget_local_file
from mediatracker.core.naming import canonical_folder_name
from mediatracker.core.precheck import REDOWNLOAD_STARTED
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem
from mediatracker.library.scanner import library_roots, scan_library

LATER = NOW + timedelta(hours=1)


def _series(db, title: str = "Brume Port", *, tmdb_id: int = 91001, **kwargs) -> Media:
    row = Media(type="series", title=title, year=2025, tmdb_id=tmdb_id, followed=True, **kwargs)
    db.add(row)
    db.flush()
    return row


def _library_row(
    db,
    *,
    media: Media,
    item: MediaItem,
    path: str,
    status: str = "matched",
) -> LibraryFile:
    row = LibraryFile(
        path=path,
        parsed_title=media.title,
        media_type=media.type,
        media_id=media.id,
        media_item_id=item.id,
        season=item.season,
        episode=item.episode,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def _film_snapshot(client, media_id: int) -> dict:
    fiche = client.get(f"/api/v1/media/{media_id}")
    assert fiche.status_code == 200, fiche.text
    listed = client.get("/api/v1/media")
    assert listed.status_code == 200, listed.text
    row = next(item for item in listed.json()["items"] if item["id"] == media_id)
    items = client.get(f"/api/v1/media/{media_id}/items")
    assert items.status_code == 200, items.text
    film = items.json()["seasons"][0]["episodes"][0]
    return {
        "fiche": fiche.json()["file_status"],
        "library": row["file_status"],
        "episode": film["file_status"],
        "progress": fiche.json()["progress"],
        "poll": fiche.json()["poll"],
        "auto_status": fiche.json()["auto_status"],
        "trash_entry_id": film.get("trash_entry_id"),
        "file_absent": film.get("file_absent"),
    }


def _series_episode_snapshot(client, media_id: int, season: int, episode: int) -> dict:
    fiche = client.get(f"/api/v1/media/{media_id}")
    assert fiche.status_code == 200, fiche.text
    listed = client.get("/api/v1/media")
    assert listed.status_code == 200, listed.text
    lib = next(item for item in listed.json()["items"] if item["id"] == media_id)
    items = client.get(f"/api/v1/media/{media_id}/items")
    assert items.status_code == 200, items.text
    season_row = next(s for s in items.json()["seasons"] if s["number"] == season)
    ep = next(e for e in season_row["episodes"] if e["episode"] == episode)
    return {
        "fiche": fiche.json()["file_status"],
        "library": lib["file_status"],
        "season": season_row["file_status"],
        "episode": ep["file_status"],
        "trash_entry_id": ep.get("trash_entry_id"),
        "file_absent": ep.get("file_absent"),
        "progress": fiche.json()["progress"],
        "next_episode": fiche.json()["next_episode"],
    }


def _assert_same_status(snap: dict, expected: str, *, keys: tuple[str, ...]) -> None:
    for key in keys:
        assert snap[key] == expected, f"{key}={snap[key]!r}, attendu {expected!r}, snap={snap}"


def test_v4_film_present_on_fiche_library_and_episode(client, app):
    login(client)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    path = _video(movies, "Zénith/Zénith.mkv")
    db = app.state.session_factory()
    try:
        media = _film(db, "Zénith", tmdb_id=92001, followed=True)
        item = _film_item(db, media, path)
        _library_row(db, media=media, item=item, path=str(path.resolve()))
        media.last_poll_result = "obtained"
        db.commit()
        media_id = media.id
    finally:
        db.close()

    snap = _film_snapshot(client, media_id)
    _assert_same_status(snap, "present", keys=("fiche", "library", "episode"))
    assert snap["poll"]["last_result"] == "obtained"


def test_v4_series_trash_restore_then_present_everywhere(client, app):
    _enable_cleanup(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    path = _video(series, "Corail Nord/Season 01/Corail.Nord.S01E01.mkv")
    db = app.state.session_factory()
    try:
        media = _series(db, "Corail Nord")
        item = _episode(db, media, path, season=1, episode=1)
        _library_row(db, media=media, item=item, path=str(path.resolve()))
        db.commit()
        media_id = media.id
        item_id = item.id
    finally:
        db.close()

    snap = _series_episode_snapshot(client, media_id, 1, 1)
    _assert_same_status(snap, "present", keys=("fiche", "library", "season", "episode"))

    removed = client.post(
        "/api/v1/cleanup/files",
        json={"media_id": media_id, "scope": "episode", "item_id": item_id, "confirm": True},
        headers=csrf(client),
    )
    assert removed.status_code == 200, removed.text
    entry_id = removed.json()["entries"][0]["id"]

    trashed = _series_episode_snapshot(client, media_id, 1, 1)
    _assert_same_status(trashed, "trash", keys=("fiche", "library", "season", "episode"))
    assert trashed["trash_entry_id"] == entry_id

    restored = client.post(f"/api/v1/trash/{entry_id}/restore", headers=csrf(client))
    assert restored.status_code == 200, restored.text
    assert path.is_file()

    back = _series_episode_snapshot(client, media_id, 1, 1)
    _assert_same_status(back, "present", keys=("fiche", "library", "season", "episode"))
    assert back["trash_entry_id"] is None


def test_v4_absent_outside_app_clears_obtained_and_offers_redownload(client, app):
    login(client)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    ghost = movies / "Brume Azur/Brume.Azur.mkv"
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_bytes(b"gone")
    db = app.state.session_factory()
    try:
        media = _film(db, "Brume Azur", tmdb_id=92002, followed=True)
        item = _film_item(db, media, ghost)
        _library_row(db, media=media, item=item, path=str(ghost.resolve()))
        media.last_poll_result = "obtained"
        media.last_poll_message = "Fichier obtenu."
        db.commit()
        media_id = media.id
        item_id = item.id
        ghost.unlink()
        forget_local_file(db, media, item, NOW)
        db.commit()
    finally:
        db.close()

    snap = _film_snapshot(client, media_id)
    _assert_same_status(snap, "absent", keys=("fiche", "library", "episode"))
    assert snap["file_absent"] is True
    assert snap["poll"]["reason"] != "film_obtained"
    assert snap["poll"]["last_result"] != "obtained"
    assert "Obtenu" not in snap["auto_status"]

    response = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert response.status_code == 200, response.text
    assert response.json()["message"] == REDOWNLOAD_STARTED


def test_v4_downloading_coherent_on_all_surfaces(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = _series(db, "Flux Veille")
        item = _episode(db, media, None, season=1, episode=2, watched_at=None)
        db.add(Download(media_item_id=item.id, state="downloading", filename="Flux.Veille.S01E02.mkv", created_at=NOW))
        db.commit()
        media_id = media.id
    finally:
        db.close()

    snap = _series_episode_snapshot(client, media_id, 1, 2)
    _assert_same_status(snap, "downloading", keys=("fiche", "library", "season", "episode"))


def test_v4_library_list_matches_fiche_after_canonical_rescan(client, app):
    """Après scan bibliothèque, liste médias et fiche exposent le même statut."""
    login(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    db = app.state.session_factory()
    try:
        media = _series(db, "Cristal Est", tmdb_id=4402)
        _episode(
            db,
            media,
            None,
            season=1,
            episode=1,
            traces=[{"kind": TRACE_FILE_ABSENT, "at": NOW.isoformat()}],
        )
        folder = series / canonical_folder_name("Cristal Est", 2025, 4402) / "Season 01"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "Cristal Est (2025) [tmdbid-4402] - S01E01.mkv"
        video.write_bytes(b"clip")
        db.commit()
        media_id = media.id
        movies, series_root = library_roots(db, app.state.settings)
        scan_library(db, movies=movies, series=series_root, now=NOW, settings=app.state.settings)
        db.commit()
    finally:
        db.close()

    snap = _series_episode_snapshot(client, media_id, 1, 1)
    _assert_same_status(snap, "present", keys=("fiche", "library", "season", "episode"))


def test_list_media_does_not_refresh_library_claims_without_scan(client, app):
    login(client)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    path = _video(movies, "Lecture Seule/Lecture.Seule.mkv")
    db = app.state.session_factory()
    try:
        media = _film(db, "Lecture Seule", tmdb_id=92010, followed=True)
        item = _film_item(db, media, path)
        _library_row(db, media=media, item=item, path=str(path.resolve()))
        db.commit()
    finally:
        db.close()

    with (
        patch("mediatracker.core.library_claims.reconcile_library_claims_after_scan") as after_scan,
        patch("mediatracker.core.source_poll.refresh_library_claims") as refresh,
    ):
        assert client.get("/api/v1/media").status_code == 200
        assert client.get("/api/v1/media").status_code == 200
    after_scan.assert_not_called()
    refresh.assert_not_called()


def test_v4_repair_report_matches_fiche_and_leaves_other_media_untouched(client, app):
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    path = _video(series, "Réparer Ici/Season 01/Reparer.Ici.S01E01.mkv")
    control_path = _video(series, "Témoin Là/Season 01/Temoin.La.S01E01.mkv")
    db = app.state.session_factory()
    try:
        target = _series(db, "Réparer Ici")
        control = _series(db, "Témoin Là", tmdb_id=91002)
        t_item = _episode(db, target, path, season=1, episode=1)
        c_item = _episode(db, control, control_path, season=1, episode=1)
        _library_row(db, media=target, item=t_item, path=str(path.resolve()))
        _library_row(db, media=control, item=c_item, path=str(control_path.resolve()))
        db.commit()
        target_id = target.id
        control_id = control.id
    finally:
        db.close()

    login(client)
    control_before = _series_episode_snapshot(client, control_id, 1, 1)

    fake_source = object()
    with (
        patch("mediatracker.providers.sources.loading.enabled_remote_sources", return_value=[(1, fake_source)]),
        patch("mediatracker.core.fiche_repair.refresh_library_claims", return_value=False),
        patch("mediatracker.core.fiche_repair.reanalyze_run") as reanalyze,
        patch("mediatracker.core.fiche_repair.reread_source_for_repair") as source,
        patch("mediatracker.core.fiche_repair._jellyfin_step", return_value=(0, "ok", None)),
    ):
        from mediatracker.library.scanner import TargetedScanReport

        reanalyze.return_value = TargetedScanReport(found=1, new=0, absent=0)
        from mediatracker.core.source_poll import SourceRereadReport

        source.return_value = SourceRereadReport(ok=True, error=None, links=1, message="1 lien.")
        repair = client.post(f"/api/v1/media/{target_id}/repair", headers=csrf(client))

    assert repair.status_code == 200, repair.text
    body = repair.json()
    assert body["file_status"] == "present"

    after = _series_episode_snapshot(client, target_id, 1, 1)
    _assert_same_status(after, "present", keys=("fiche", "library", "season", "episode"))
    assert after["fiche"] == body["file_status"]

    control_after = _series_episode_snapshot(client, control_id, 1, 1)
    assert control_after == control_before
