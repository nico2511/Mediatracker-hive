"""One library report: duplicate folders, mismatched files, and dry-run moves."""

from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.db.models import Media
from mediatracker.library.review import apply_merge, build_review


def _library(tmp_path: Path) -> tuple[Path, Path]:
    series = tmp_path / "Series"
    tagged = series / "Suite Ville Démo... (2021) [tmdbid-900002]" / "Season 03"
    plain = series / "Suite Ville Démo... (2021)" / "Season 02"
    tagged.mkdir(parents=True)
    plain.mkdir(parents=True)
    for number in (1, 2):
        (tagged / f"Suite.Ville.Démo.S03E{number:02d}.mkv").write_bytes(b"a")
        (plain / f"Suite.Ville.Démo.S02E{number:02d}.mkv").write_bytes(b"b")
    expedition = series / "Expédition Démo (2022)" / "Season 01"
    expedition.mkdir(parents=True)
    (expedition / "Détective Démo.S01E11.mkv").write_bytes(b"w")
    (expedition / "Expédition.Démo.S01E01.mkv").write_bytes(b"t")
    empty = series / "Vide"
    empty.mkdir()
    (empty / "movie.nfo").write_text("nfo", encoding="utf-8")
    return tmp_path / "Films", series


def test_review_groups_one_series_and_keeps_files_until_apply(tmp_path, client, app):
    del client
    films, series = _library(tmp_path)
    db = app.state.session_factory()
    try:
        db.add(
            Media(
                type="series",
                title="Suite Ville Démo...",
                year=2021,
                tmdb_id=900002,
                poster_url="https://image.tmdb.org/t/p/w92/demo_suite.jpg",
                followed=True,
            )
        )
        db.commit()
        report = build_review(db, films, series)
        demo_suite = next(row for row in report.series if row.tmdb_id == 900002)
        assert len(demo_suite.folders) == 2
        assert demo_suite.duplicate is True
        assert demo_suite.poster_url == "https://image.tmdb.org/t/p/w92/demo_suite.jpg"
        labels = {season.label for folder in demo_suite.folders for season in folder.seasons}
        assert labels == {"S02 · 2 ép.", "S03 · 2 ép."}
        assert any(folder.canonical and "tmdbid-900002" in folder.name for folder in demo_suite.folders)
        assert demo_suite.merges
        plain = series / "Suite Ville Démo... (2021)" / "Season 02" / "Suite.Ville.Démo.S02E01.mkv"
        assert plain.is_file()
        expedition_row = next(row for row in report.series if row.title.startswith("Expédition"))
        assert any(
            item.kind == "mismatch" and item.name.startswith("Détective Démo") for item in expedition_row.files
        )
        assert any(row.path.endswith("/Vide") or row.path.endswith("Vide") for row in report.empty)

        applied, rename_id, _pending = apply_merge(db, films, series, demo_suite.key, validated=False)
        assert applied is False
        assert rename_id is None
        assert plain.is_file()

        applied, rename_id, _pending = apply_merge(db, films, series, demo_suite.key, validated=True)
        db.commit()
        assert applied is True
        assert rename_id is not None
        dest = (
            series
            / "Suite Ville Démo... (2021) [tmdbid-900002]"
            / "Season 02"
            / "Suite.Ville.Démo.S02E01.mkv"
        )
        assert dest.is_file()
        assert not plain.is_file()
    finally:
        db.close()


def test_review_endpoint_is_a_dry_run(client, app):
    films, series = _library(Path(app.state.settings.data_dir) / "media")
    del films
    login(client)
    listed = client.get("/api/v1/library/review")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    group = next(row for row in body["series"] if row["tmdb_id"] == 900002)
    assert len(group["folders"]) == 2
    assert group["duplicate"] is True
    assert group["poster_url"] is None or isinstance(group["poster_url"], str)
    plain = series / "Suite Ville Démo... (2021)" / "Season 02" / "Suite.Ville.Démo.S02E01.mkv"
    preview = client.post(
        "/api/v1/library/review/merge",
        json={"key": group["key"], "validated": False},
        headers=csrf(client),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["applied"] is False
    assert plain.is_file()
