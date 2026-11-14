"""Keep the normalized episode and trash the release name, for any series.

The two copies sit in different folders. One is already
``Title (Year) - SxxExx`` inside the tmdbid folder. The other keeps a release
name. Pairing is by the resolved series (TMDB, fiche, or Jellyfin id) and
``SxxExx``, not by the folder path. A nearby title is a different series.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from mediatracker.db.models import JellyfinLibraryItem, Media, TrashEntry
from mediatracker.library.audit import apply_audit, audit_library
from mediatracker.library.review import build_review

HARBOR_RELEASE = "North Harbors Tale FRENCH DDP5 1 BraD Wawacity irish - S01E{episode:02d}.mkv"
HARBOR_NORMAL = "North Harbor's Tale (2024) - S01E{episode:02d}.mkv"
HARBOR_TAGGED = "North Harbor's Tale (2024) [tmdbid-4401]"
HARBOR_PLAIN = "North Harbor's Tale"

CEDAR_RELEASE = "Cedar.Post.S01E{episode:02d}.WEB.mkv"
CEDAR_NORMAL = "Cedar Post (2019) - S01E{episode:02d}.mkv"
CEDAR_TAGGED = "Cedar Post (2019) [tmdbid-4402]"
CEDAR_PLAIN = "Cedar Post"

OFFICE_RELEASE = "Cedar.Post.Office.S01E{episode:02d}.WEB.mkv"
OFFICE_NORMAL = "Cedar Post Office (2019) - S01E{episode:02d}.mkv"
OFFICE_TAGGED = "Cedar Post Office (2019) [tmdbid-4403]"
OFFICE_PLAIN = "Cedar Post Office"


def _sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.truncate(path, size)


def _show(
    series: Path,
    tagged: str,
    plain: str,
    normal: str,
    release: str,
    episodes: range,
    sizes: dict[int, tuple[int, int]],
) -> None:
    for episode in episodes:
        kept, other = sizes[episode]
        _sparse(series / tagged / "Season 01" / normal.format(episode=episode), kept)
        _sparse(series / plain / "Season 01" / release.format(episode=episode), other)


def test_several_series_pair_by_identity_and_episode_not_by_folder(tmp_path, client, app):
    """One library holds three series. Close titles stay apart. The plain folder has no year and no tmdbid."""
    del client
    films = tmp_path / "Films"
    series = tmp_path / "Series"
    assert "(" not in HARBOR_PLAIN and "tmdbid" not in HARBOR_PLAIN
    _show(
        series,
        HARBOR_TAGGED,
        HARBOR_PLAIN,
        HARBOR_NORMAL,
        HARBOR_RELEASE,
        range(1, 4),
        {
            1: (1000, 400 * 1024 * 1024),
            2: (80 * 1024 * 1024, 100 * 1024 * 1024),
            3: (80 * 1024 * 1024, 100 * 1024 * 1024),
        },
    )
    _show(
        series,
        CEDAR_TAGGED,
        CEDAR_PLAIN,
        CEDAR_NORMAL,
        CEDAR_RELEASE,
        range(1, 3),
        {1: (70 * 1024 * 1024, 90 * 1024 * 1024), 2: (70 * 1024 * 1024, 90 * 1024 * 1024)},
    )
    _show(
        series,
        OFFICE_TAGGED,
        OFFICE_PLAIN,
        OFFICE_NORMAL,
        OFFICE_RELEASE,
        range(1, 2),
        {1: (70 * 1024 * 1024, 90 * 1024 * 1024)},
    )
    db = app.state.session_factory()
    try:
        db.add(
            Media(
                type="series",
                title="North Harbor's Tale",
                year=2024,
                tmdb_id=4401,
                jellyfin_series_id="jf-harbor-4401",
                followed=True,
            )
        )
        db.add(Media(type="series", title="Cedar Post", year=2019, tmdb_id=4402, followed=True))
        db.add(Media(type="series", title="Cedar Post Office", year=2019, tmdb_id=4403, followed=True))
        db.commit()
        report = build_review(db, films, series)
        harbor = next(row for row in report.series if row.tmdb_id == 4401)
        cedar = next(row for row in report.series if row.tmdb_id == 4402)
        office = next(row for row in report.series if row.tmdb_id == 4403)

        assert harbor.title == "North Harbor's Tale"
        assert {folder.name for folder in harbor.folders} == {HARBOR_TAGGED, HARBOR_PLAIN}
        assert any(folder.canonical and folder.name == HARBOR_TAGGED for folder in harbor.folders)
        assert harbor.merges == ()
        assert harbor.problem == "duplicate"
        assert any(Path(path).name == HARBOR_PLAIN for path in harbor.empty_after)
        kept = {HARBOR_NORMAL.format(episode=episode) for episode in range(1, 4)}
        assert kept.isdisjoint({item.name for item in harbor.files})
        assert len(harbor.files) == 3
        for item in harbor.files:
            assert item.kind == "duplicate"
            assert item.action == "trash"
            assert item.target_path == ""
            assert item.target_name == ""
            assert item.blocked is None
            assert "Aucun dossier" not in item.reason
            assert "conservée" in item.reason
            assert item.name.startswith("North Harbors Tale FRENCH")
            assert item.other_size is not None
        first = next(item for item in harbor.files if item.name.endswith("S01E01.mkv"))
        assert first.warning is not None
        assert "très petite" in first.warning
        later = next(item for item in harbor.files if item.name.endswith("S01E02.mkv"))
        assert later.warning is None

        assert {item.name for item in cedar.files} == {CEDAR_RELEASE.format(episode=episode) for episode in range(1, 3)}
        assert all(item.action == "trash" and item.target_name == "" for item in cedar.files)
        assert cedar.merges == ()
        assert {item.name for item in office.files} == {OFFICE_RELEASE.format(episode=1)}
        assert office.files[0].action == "trash"
        assert {item.name for item in harbor.files}.isdisjoint({item.name for item in cedar.files})
        assert {item.name for item in cedar.files}.isdisjoint({item.name for item in office.files})

        audit = audit_library(films, series)
        cedar_kept = {CEDAR_NORMAL.format(episode=episode) for episode in range(1, 3)}
        normalized = kept | cedar_kept | {OFFICE_NORMAL.format(episode=1)}
        assert all(item.action != "trash" or item.name not in normalized for item in audit.items)
        assert not any(item.name in normalized and item.target_name == item.name for item in audit.items)
        assert sum(1 for item in audit.items if item.action == "trash") == 3 + 2 + 1

        release = series / HARBOR_PLAIN / "Season 01" / HARBOR_RELEASE.format(episode=1)
        preview = apply_audit(db, films, series, [str(release)], validated=False)
        assert preview.applied is False
        assert release.is_file()
        plain = series / HARBOR_PLAIN / "Season 01"
        applied = apply_audit(db, films, series, [str(path) for path in plain.glob("*.mkv")], validated=True)
        db.commit()
        assert applied.applied is True
        assert applied.rename_id is None
        tagged = series / HARBOR_TAGGED / "Season 01"
        assert all((tagged / HARBOR_NORMAL.format(episode=episode)).is_file() for episode in range(1, 4))
        assert not any(plain.glob("*.mkv"))
        assert (series / CEDAR_PLAIN / "Season 01" / CEDAR_RELEASE.format(episode=1)).is_file()
        assert (series / OFFICE_PLAIN / "Season 01" / OFFICE_RELEASE.format(episode=1)).is_file()
        held = list(db.scalars(select(TrashEntry)))
        assert len(held) == 3
        assert {row.reason for row in held} == {"duplicate"}
        assert all(Path(row.trash_path).is_file() for row in held)
        after = build_review(db, films, series)
        assert any(Path(row.path).name == HARBOR_PLAIN for row in after.empty)
    finally:
        db.close()


def test_same_folder_normalized_copy_is_not_renamed_onto_itself(tmp_path):
    series = tmp_path / "Series"
    folder = series / HARBOR_TAGGED / "Season 01"
    kept = folder / HARBOR_NORMAL.format(episode=1)
    release = folder / HARBOR_RELEASE.format(episode=1)
    _sparse(kept, 80 * 1024 * 1024)
    _sparse(release, 90 * 1024 * 1024)
    report = audit_library(tmp_path / "Films", series)
    assert all(item.path != str(kept.resolve()) for item in report.items)
    row = next(item for item in report.items if item.name == release.name)
    assert row.kind == "duplicate"
    assert row.action == "trash"
    assert row.target_path == ""
    assert row.target_name == ""
    assert "Proposition" not in row.reason
    assert kept.name in row.reason
    assert row.warning is None


def test_different_tmdb_ids_are_not_paired(tmp_path):
    series = tmp_path / "Series"
    left = series / "Bright Kiln (2021) [tmdbid-11]" / "Season 01"
    right = series / "Bright Kiln (2021) [tmdbid-22]" / "Season 01"
    _sparse(left / "Bright Kiln (2021) - S01E01.mkv", 1024)
    _sparse(right / "Bright Kiln (2021) - S01E01.mkv", 1024)
    report = audit_library(tmp_path / "Films", series)
    assert all(item.action != "trash" for item in report.items)


def test_jellyfin_series_id_pairs_a_release_copy_in_another_folder(tmp_path, client, app):
    del client
    series = tmp_path / "Series"
    home = series / "Mesa Line (2018) [tmdbid-77]" / "Season 01"
    kept = home / "Mesa Line (2018) - S01E01.mkv"
    stray = series / "Unrelated Shelf"
    release = stray / "Mesa.Line.S01E01.WEB.mkv"
    _sparse(kept, 80 * 1024 * 1024)
    _sparse(release, 90 * 1024 * 1024)
    db = app.state.session_factory()
    try:
        moment = datetime(2026, 10, 16, tzinfo=timezone.utc)
        db.add(
            Media(
                type="series",
                title="Mesa Line",
                year=2018,
                tmdb_id=77,
                jellyfin_series_id="jf-mesa",
                followed=True,
            )
        )
        db.add(
            JellyfinLibraryItem(
                jellyfin_id="ep-mesa",
                item_type="Episode",
                name="Episode 1",
                series_id="jf-mesa",
                season=1,
                episode=1,
                local_path=str(release),
                path=str(release),
                synced_at=moment,
            )
        )
        db.commit()
        report = build_review(db, tmp_path / "Films", series)
        group = next(row for row in report.series if row.tmdb_id == 77)
        assert {folder.name for folder in group.folders} == {"Mesa Line (2018) [tmdbid-77]", "Unrelated Shelf"}
        assert [item.name for item in group.files] == [release.name]
        assert group.files[0].action == "trash"
        assert group.merges == ()
        assert all(item.path != str(kept.resolve()) for item in group.files)
    finally:
        db.close()
