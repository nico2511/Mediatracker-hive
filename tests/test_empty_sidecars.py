"""Sidecar-only folders and leftover thumbs are cleanup suggestions, moved to trash."""

from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.library_plan import preview_empty_folders, remove_empty_folders
from mediatracker.db.models import TrashEntry

_RELEASE = "Neuf.Hôtes.Démo.S02E01.FRENCH.1080p.WEB.H264-Wawacity.xxx"


def _season(root: Path) -> Path:
    folder = root / "Neuf Hôtes Démo (2021)" / "Season 02"
    folder.mkdir(parents=True)
    return folder


def _sidecars(folder: Path) -> dict[str, bytes]:
    files = {
        "season02-poster.jpg": b"poster",
        "logo.svg": b"<svg/>",
        f"{_RELEASE}-thumb.jpg": b"thumb-bytes",
        f"{_RELEASE}.nfo": b"<nfo/>",
    }
    for name, payload in files.items():
        (folder / name).write_bytes(payload)
    return files


def test_sidecar_only_season_is_effectively_empty_and_trashed(tmp_path: Path):
    series = tmp_path / "Series"
    season = _season(series)
    files = _sidecars(season)
    show = series / "Neuf Hôtes Démo (2021)"
    (show / "tvshow.nfo").write_text("show", encoding="utf-8")
    trash = tmp_path / "trash"

    rows = preview_empty_folders(series)
    paths = {row.path: row.summary for row in rows}
    assert str(season.resolve()) in paths
    assert "Effectivement vide" in paths[str(season.resolve())]
    assert ".svg" in paths[str(season.resolve())]
    assert str(season / f"{_RELEASE}-thumb.jpg") not in paths
    assert all(not path.endswith(".mkv") for path in paths)

    moved = []
    removed = remove_empty_folders([str(season)], series, trash=trash, moved=moved)
    assert str(season.resolve()) in removed
    assert not season.exists()
    assert show.is_dir()
    trashed = {Path(item.trash_path).name: Path(item.trash_path).read_bytes() for item in moved}
    for name, payload in files.items():
        assert trashed[name] == payload
        assert not (season / name).exists()
    assert trash.is_dir()
    assert any(path.is_file() for path in trash.rglob("*"))


def test_a_folder_with_video_is_never_proposed_but_orphan_sidecars_are(tmp_path: Path):
    series = tmp_path / "Series"
    season = _season(series)
    _sidecars(season)
    video = season / "Neuf Hôtes Démo (2021) - S02E01.mkv"
    video.write_bytes(b"video-bytes")
    (season / f"{video.name}-thumb.jpg").write_bytes(b"live-thumb")
    (season / "tvshow.nfo").write_text("show", encoding="utf-8")
    subtitle = season / "Neuf Hôtes Démo (2021) - S02E01.srt"
    subtitle.write_text("sub", encoding="utf-8")
    trash = tmp_path / "trash"

    rows = preview_empty_folders(series)
    paths = {row.path for row in rows}
    assert str(season.resolve()) not in paths
    assert str(video) not in paths
    assert str(season / "season02-poster.jpg") not in paths
    assert str(season / "logo.svg") not in paths
    assert str(season / f"{video.name}-thumb.jpg") not in paths
    assert str(season / "tvshow.nfo") not in paths
    thumb = season / f"{_RELEASE}-thumb.jpg"
    nfo = season / f"{_RELEASE}.nfo"
    assert str(thumb.resolve()) in paths
    assert str(nfo.resolve()) in paths
    assert all("Orphelin" in row.summary for row in rows if row.path in {str(thumb.resolve()), str(nfo.resolve())})

    removed = remove_empty_folders([str(season), str(video), str(thumb), str(nfo)], series, trash=trash)
    assert str(season.resolve()) not in removed
    assert str(video.resolve()) not in removed
    assert video.read_bytes() == b"video-bytes"
    assert (season / f"{video.name}-thumb.jpg").read_bytes() == b"live-thumb"
    assert subtitle.is_file()
    assert (season / "season02-poster.jpg").is_file()
    assert (season / "logo.svg").is_file()
    assert not thumb.exists()
    assert not nfo.exists()
    held = [path.read_bytes() for path in trash.rglob("*") if path.is_file()]
    assert b"thumb-bytes" in held
    assert b"<nfo/>" in held
    assert b"video-bytes" not in held


def test_cleanup_api_moves_a_sidecar_season_to_trash(client, app):
    login(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    season = _season(series)
    payload = b"thumb-bytes"
    thumb = season / f"{_RELEASE}-thumb.jpg"
    thumb.write_bytes(payload)
    (season / "season02-poster.jpg").write_bytes(b"poster")
    (season / "logo.svg").write_bytes(b"<svg/>")
    (season / f"{_RELEASE}.nfo").write_bytes(b"<nfo/>")
    kept = series / "Other (2020)" / "Season 01"
    kept.mkdir(parents=True)
    video = kept / "Other (2020) - S01E01.mkv"
    video.write_bytes(b"stay")

    review = client.get("/api/v1/library/review")
    assert review.status_code == 200, review.text
    summaries = {item["path"]: item["summary"] for item in review.json()["empty"]}
    assert "Effectivement vide" in summaries[str(season.resolve())]
    assert str(kept.resolve()) not in summaries

    cleaned = client.post(
        "/api/v1/library/empty-folders",
        json={"paths": [str(season)]},
        headers=csrf(client),
    )
    assert cleaned.status_code == 200, cleaned.text
    assert str(season.resolve()) in cleaned.json()["paths"]
    assert not season.exists()
    assert not thumb.exists()
    assert video.read_bytes() == b"stay"
    db = app.state.session_factory()
    try:
        rows = list(db.scalars(select(TrashEntry)).all())
    finally:
        db.close()
    assert rows
    assert all(row.reason == "empty" for row in rows)
    assert any(Path(row.trash_path).is_file() and Path(row.trash_path).read_bytes() == payload for row in rows)
