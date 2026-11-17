"""Release-named series folders regroup onto the tracked fiche, and only on confirm."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from tests.conftest import csrf, login

from mediatracker.core.naming import ID_CONFLICT, filing_folder, library_destination
from mediatracker.db.models import LibraryFile, LibraryRename, Media, Setting, TrashEntry
from mediatracker.library.audit import apply_audit, audit_library
from mediatracker.library.parse import parse_release, strip_release_junk
from mediatracker.library.regroup import apply_regroup, plan_regroup


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        ("Détective Démo FRENCH DDP5 1 BraD Wawacity town", "Détective Démo"),
        ("Détective Démo DDP5 1 BraD Wawacity diy", "Détective Démo"),
        ("Détective Démo.FRENCH.DDP5.1.BraD.Wawacity.town.S01E02.mkv", "Détective Démo"),
        ("Immeuble Démo FRENCH TFA Wawacity town", "Immeuble Démo"),
        ("Immeuble Démo FRENCH FW Wawacity diy", "Immeuble Démo"),
        ("Héritage Démo GL0P Wawacity press", "Héritage Démo"),
        ("Trésor Démo FRENCH HiggsBoson Wawacity news", "Trésor Démo"),
        ("Forteresse Démo SUPPLY Wawacity estate", "Forteresse Démo"),
        ("Refuge Sorcellerie Démo FRENCH DDP5 1 BraD Wawacity irish", "Refuge Sorcellerie Démo"),
    ],
)
def test_wawacity_suffix_is_not_part_of_the_title(raw, title):
    if raw.endswith(".mkv"):
        assert parse_release(raw).title == title
    else:
        assert strip_release_junk(raw) == title


def test_download_folder_follows_the_fiche_id(tmp_path):
    series = tmp_path / "tvshows"
    adopted = series / "Détective Démo FRENCH DDP5 1 BraD Wawacity town"
    adopted.mkdir(parents=True)
    tagged = series / "Détective Rose Démo (2020) [tmdbid-900105]"
    tagged.mkdir(parents=True)
    junk_tagged = series / "Détective Démo SUPPLY Wawacity estate [tmdbid-2511]"
    junk_tagged.mkdir(parents=True)
    dest = library_destination(
        movies_dir=tmp_path / "films",
        series_dir=series,
        media_type="series",
        title="Détective Démo",
        year=2025,
        season=1,
        episode=4,
        ext=".mkv",
        tmdb_id=2511,
        library_folder=filing_folder(series, title="Détective Démo", year=2025, tmdb_id=2511),
    )
    assert dest.parent.parent.name == "Détective Démo (2025) [tmdbid-2511]"
    assert "Wawacity" not in str(dest)
    assert filing_folder(series, title="Détective Rose Démo", year=2020, tmdb_id=900105) == tagged


def test_audit_does_not_invent_a_release_folder(tmp_path, client, app):
    del client
    series = tmp_path / "Series"
    folder = series / "Divers"
    folder.mkdir(parents=True)
    src = folder / "Détective Démo.FRENCH.DDP5.1.BraD.Wawacity.town.S01E01.mkv"
    src.write_bytes(b"v")
    report = audit_library(tmp_path / "Films", series)
    assert report.mismatch == 1
    assert all("Wawacity" not in item.target_folder for item in report.items)
    db = app.state.session_factory()
    try:
        apply_audit(db, tmp_path / "Films", series, [str(src)], validated=True)
        db.commit()
    finally:
        db.close()
    assert src.is_file()
    assert not any("Wawacity" in path.name for path in series.iterdir())


def _shows(series: Path) -> tuple[Path, Path, Path]:
    junk = series / "Détective Démo FRENCH DDP5 1 BraD Wawacity town"
    (junk / "Season 01").mkdir(parents=True)
    episode = junk / "Season 01" / "Détective Démo.FRENCH.DDP5.1.BraD.Wawacity.town.S01E04.mkv"
    episode.write_bytes(b"v")
    (junk / "Season 01" / "plain.mkv").write_bytes(b"p")
    (junk / "tvshow.nfo").write_text("nfo", encoding="utf-8")
    demo_building_release = series / "Immeuble Démo FRENCH TFA Wawacity surf"
    (demo_building_release / "Season 02").mkdir(parents=True)
    (demo_building_release / "Season 02" / "Immeuble.Démo.S02E01.mkv").write_bytes(b"m")
    tresor_release = series / "Trésor Démo FRENCH HiggsBoson Wawacity news"
    (tresor_release / "Season 01").mkdir(parents=True)
    (tresor_release / "Season 01" / "Trésor.Démo.S01E01.mkv").write_bytes(b"g")
    rose_folder = series / "Détective Rose Démo (2020) [tmdbid-900105]" / "Season 01"
    rose_folder.mkdir(parents=True)
    (rose_folder / "Détective Rose Démo (2020) - S01E01.mkv").write_bytes(b"s")
    empty = series / "Vide"
    empty.mkdir()
    (empty / "movie.nfo").write_text("n", encoding="utf-8")
    return junk, demo_building_release, tresor_release


def test_regroup_is_a_dry_run_until_confirmed(tmp_path, client, app):
    del client
    series = tmp_path / "tvshows"
    junk, demo_building_release, tresor_release = _shows(series)
    episode = junk / "Season 01" / "Détective Démo.FRENCH.DDP5.1.BraD.Wawacity.town.S01E04.mkv"
    db = app.state.session_factory()
    try:
        detective_demo = Media(type="series", title="Détective Démo", year=2025, tmdb_id=2511, followed=True)
        building = Media(
            type="series",
            title="Immeuble Démo",
            year=2021,
            tmdb_id=900001,
            followed=True,
        )
        money = Media(
            type="series",
            title="Trésor Démo",
            year=2026,
            tmdb_id=900106,
            followed=True,
        )
        rose_demo = Media(
            type="series",
            title="Détective Rose Démo",
            year=2020,
            tmdb_id=900105,
            followed=True,
        )
        db.add_all([detective_demo, building, money, rose_demo])
        db.flush()
        db.add(
            LibraryFile(
                path=str(episode.resolve()),
                size=1,
                parsed_title="Détective Démo",
                media_type="series",
                original_name="Détective Démo.FRENCH.DDP5.1.BraD.Wawacity.town.S01E04.mkv",
                file_source="wawacity",
                status="matched",
                seen_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
            )
        )
        db.commit()
        films = tmp_path / "Films"
        report = plan_regroup(db, films, series)
        by_id = {group.media_id: group for group in report.groups}
        assert rose_demo.id not in by_id
        detective_plan = by_id[detective_demo.id]
        assert detective_plan.match == "titre"
        assert detective_plan.canonical.endswith("Détective Démo (2025) [tmdbid-2511]")
        names = {move.name for move in detective_plan.moves}
        assert "Détective Démo (2025) - S01E04.mkv" in names
        assert "plain.mkv" in names
        assert "tvshow.nfo" in names
        kept = next(move for move in detective_plan.moves if move.name == "Détective Démo (2025) - S01E04.mkv")
        assert kept.original_name == episode.name
        assert kept.action == "move"
        assert kept.dest.endswith(f"Season 01/{kept.name}")
        assert not kept.blocked
        plain = next(move for move in detective_plan.moves if move.name == "plain.mkv")
        assert plain.dest.endswith("Season 01/plain.mkv")
        assert by_id[building.id].canonical.endswith("Immeuble Démo (2021) [tmdbid-900001]")
        assert by_id[money.id].match == "titre"
        assert any(path.endswith("/Vide") or path.endswith("Vide") for path in report.empty)
        assert episode.is_file()

        applied, rename_id, _preview = apply_regroup(db, films, series, [detective_demo.id], validated=False)
        assert applied is False
        assert rename_id is None
        assert episode.is_file()

        applied, rename_id, after = apply_regroup(
            db, films, series, [detective_demo.id, building.id, money.id], validated=True
        )
        db.commit()
        assert applied is True
        assert rename_id is not None
        dest = (
            series
            / "Détective Démo (2025) [tmdbid-2511]"
            / "Season 01"
            / "Détective Démo (2025) - S01E04.mkv"
        )
        assert dest.is_file()
        assert not episode.exists()
        assert not junk.exists()
        assert (series / "Détective Démo (2025) [tmdbid-2511]" / "tvshow.nfo").is_file()
        building_dest = (
            series
            / "Immeuble Démo (2021) [tmdbid-900001]"
            / "Season 02"
            / "Immeuble Démo (2021) - S02E01.mkv"
        )
        assert building_dest.is_file()
        assert not demo_building_release.exists()
        assert not tresor_release.exists()
        money_file = (
            series
            / "Trésor Démo (2026) [tmdbid-900106]"
            / "Season 01"
            / "Trésor Démo (2026) - S01E01.mkv"
        )
        assert money_file.is_file()
        stored = db.query(LibraryFile).filter(LibraryFile.path == str(dest.resolve())).one()
        assert stored.original_name == episode.name
        logged = db.get(LibraryRename, rename_id)
        assert logged is not None
        assert logged.moves[0]["original_name"]
        assert detective_demo.id not in {group.media_id for group in after.groups}

        from mediatracker.core.library_plan import undo_rename

        undo_rename(db, rename_id)
        db.commit()
        assert episode.is_file()
        assert not dest.exists()
        assert db.query(LibraryFile).one().path == str(episode.resolve())
    finally:
        db.close()


def test_regroup_reports_conflicts_and_ambiguous_titles(tmp_path, client, app):
    del client
    series = tmp_path / "tvshows"
    junk = series / "Forteresse Démo SUPPLY Wawacity estate"
    (junk / "Season 01").mkdir(parents=True)
    name = "Forteresse.Démo.S01E01.mkv"
    (junk / "Season 01" / name).write_bytes(b"a")
    canonical = series / "Forteresse Démo (2022) [tmdbid-900004]" / "Season 01"
    canonical.mkdir(parents=True)
    (canonical / name).write_bytes(b"already")
    ambiguous = series / "Détective Démo FRENCH BraD Wawacity farm"
    (ambiguous / "Season 01").mkdir(parents=True)
    (ambiguous / "Season 01" / "Détective Démo.S01E01.mkv").write_bytes(b"w")
    db = app.state.session_factory()
    try:
        dragon = Media(type="series", title="Forteresse Démo", year=2022, tmdb_id=900004, followed=True)
        db.add(dragon)
        db.add(Media(type="series", title="Détective Démo", year=2025, tmdb_id=1, followed=True))
        db.add(Media(type="series", title="Détective Démo", year=2012, tmdb_id=2, followed=True))
        db.commit()
        report = plan_regroup(db, tmp_path / "Films", series)
        group = next(row for row in report.groups if row.media_id == dragon.id)
        canonical_name = "Forteresse Démo (2022) - S01E01.mkv"
        kept = next(move for move in group.moves if move.action == "move" and move.name == canonical_name)
        assert not kept.blocked
        discarded = next(move for move in group.moves if move.action == "trash")
        assert discarded.name == name
        assert any(path.endswith("farm") or path.endswith("Wawacity farm") for path in report.ambiguous)
        applied, rename_id, _after = apply_regroup(db, tmp_path / "Films", series, [dragon.id], validated=True)
        db.commit()
        assert applied is True
        assert rename_id is not None
        assert not (junk / "Season 01" / name).exists()
        kept_file = canonical / canonical_name
        assert kept_file.is_file()
        assert kept_file.read_bytes() == b"already"
        trashed = list((tmp_path / ".trash").rglob(name))
        assert len(trashed) == 1
        assert trashed[0].is_file()
        assert trashed[0].read_bytes() == b"a"
    finally:
        db.close()


def test_regroup_endpoint_does_not_move_until_validated(client, app):
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    junk, _demo_building_release, _tresor_release = _shows(series)
    episode = next(junk.rglob("*.mkv"))
    db = app.state.session_factory()
    try:
        db.add(Media(type="series", title="Détective Démo", year=2025, tmdb_id=2511, followed=True))
        db.add(
            Media(
                type="series",
                title="Immeuble Démo",
                year=2021,
                tmdb_id=900001,
                followed=True,
            )
        )
        db.add(Media(type="series", title="Trésor Démo", year=2026, tmdb_id=900106, followed=True))
        db.commit()
    finally:
        db.close()
    login(client)
    listed = client.get("/api/v1/library/regroup")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert any(group["canonical"].endswith("[tmdbid-2511]") for group in body["groups"])
    assert body["applied"] is False
    detective_demo = next(group for group in body["groups"] if group["canonical"].endswith("[tmdbid-2511]"))
    summary = {
        "fiches": len(body["groups"]),
        "moves": sum(1 for move in detective_demo["moves"] if not move["blocked"] and move["action"] != "trash"),
        "trash": sum(1 for move in detective_demo["moves"] if move["action"] == "trash"),
    }
    assert summary["fiches"] >= 1
    assert summary["moves"] >= 1
    assert any(
        move["name"] == "Détective Démo (2025) - S01E04.mkv" and move["action"] == "move"
        for move in detective_demo["moves"]
    )
    assert episode.is_file()
    preview = client.post(
        "/api/v1/library/regroup",
        json={"validated": False, "media_ids": [body["groups"][0]["media_id"]]},
        headers=csrf(client),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["rename_id"] is None
    assert preview.json()["applied"] is False
    assert episode.is_file()


def test_regroup_does_not_file_a_tagged_folder_under_another_id(tmp_path, client, app):
    del client
    series = tmp_path / "tvshows"
    foreign = series / "Lanternes Démo (2026) [tmdbid-900103]" / "Season 01"
    foreign.mkdir(parents=True)
    episode = foreign / "Lanternes Démo (2026) - S01E01.mkv"
    episode.write_bytes(b"lanterns")
    nested = series / "Détective Démo FRENCH DDP5 1 BraD Wawacity town" / "Season 01" / "Elsewhere [tmdbid-900103]"
    nested.mkdir(parents=True)
    stolen = nested / "Détective Démo.S01E03.mkv"
    stolen.write_bytes(b"detective")
    detective_demo_home = series / "Détective Démo (2025) [tmdbid-2511]" / "Season 01"
    detective_demo_home.mkdir(parents=True)
    kept = detective_demo_home / "old.mkv"
    kept.write_bytes(b"kept")
    db = app.state.session_factory()
    try:
        db.add(Media(type="series", title="Lanternes Démo", year=2011, tmdb_id=900102, followed=True))
        detective_demo = Media(type="series", title="Détective Démo", year=2025, tmdb_id=2511, followed=True)
        db.add(detective_demo)
        db.commit()
        films = tmp_path / "Films"
        report = plan_regroup(db, films, series)
        assert str(foreign.parent.resolve()) in report.unmatched
        detective_plan = next(group for group in report.groups if group.media_id == detective_demo.id)
        assert detective_plan.canonical.endswith("Détective Démo (2025) [tmdbid-2511]")
        assert all(ID_CONFLICT != move.blocked or "900103" in move.src for move in detective_plan.moves)
        crossed = [move for move in detective_plan.moves if "900103" in move.src]
        assert crossed and all(move.blocked == ID_CONFLICT for move in crossed)
        assert all(move.blocked != ID_CONFLICT for move in detective_plan.moves if "tmdbid-2511" in move.src)
        applied, _rename_id, _after = apply_regroup(db, films, series, [detective_demo.id], validated=True)
        db.commit()
        assert episode.is_file()
        assert stolen.is_file()
        assert not applied or stolen.is_file()
    finally:
        db.close()


def _sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.truncate(path, size)


def test_refuge_sorcellerie_keeps_the_normalized_copy_and_warns_about_size(tmp_path, client, app):
    del client
    series = tmp_path / "tvshows"
    home = series / "Refuge Sorcellerie Démo (2024) [tmdbid-900107]" / "Season 01"
    kept = home / "Refuge Sorcellerie Démo (2024) - S01E01.mkv"
    _sparse(kept, 1024)
    stray = series / "Refuge.Sorcellerie.Démo.S01E01.1080p.WEB.H264-GL0P"
    release = stray / "Refuge.Sorcellerie.Démo.S01E01.1080p.WEB.H264-GL0P.mkv"
    _sparse(release, 400 * 1024 * 1024)
    db = app.state.session_factory()
    try:
        show = Media(
            type="series",
            title="Refuge Sorcellerie Démo",
            year=2024,
            tmdb_id=900107,
            followed=False,
        )
        db.add(show)
        db.commit()
        report = plan_regroup(db, tmp_path / "Films", series)
        group = next(row for row in report.groups if row.media_id == show.id)
        assert str(stray.resolve()) in group.folders
        trash = [move for move in group.moves if move.action == "trash"]
        assert len(trash) == 1
        assert trash[0].src == str(release.resolve())
        assert trash[0].size == release.stat().st_size
        assert trash[0].other_size == kept.stat().st_size
        assert trash[0].warning is not None
        assert "très petite" in trash[0].warning
        assert "nettement plus grosse" in trash[0].warning
        assert not any(move.action == "trash" and move.src == str(kept.resolve()) for move in group.moves)
        applied, _rename_id, after = apply_regroup(db, tmp_path / "Films", series, [show.id], validated=False)
        assert applied is False
        assert release.is_file() and kept.is_file()
        applied, _rename_id, after = apply_regroup(db, tmp_path / "Films", series, [show.id], validated=True)
        db.commit()
        assert applied is True
        assert kept.is_file()
        assert not release.exists()
        held = list((tmp_path / ".trash").rglob(release.name))
        assert len(held) == 1
        assert held[0].is_file()
        assert after.groups == () or show.id not in {row.media_id for row in after.groups}
    finally:
        db.close()


def test_loose_duplicate_without_a_fiche_trashes_the_release_name(tmp_path, client, app):
    del client
    series = tmp_path / "tvshows"
    home = series / "Refuge Sorcellerie Démo (2024)" / "Season 01"
    kept = home / "Refuge Sorcellerie Démo (2024) - S01E01.mkv"
    _sparse(kept, 80 * 1024 * 1024)
    stray = series / "Refuge.Sorcellerie.Démo.S01E01.1080p.WEB"
    release = stray / "Refuge.Sorcellerie.Démo.S01E01.FiNAL.1080p.WEB.mkv"
    _sparse(release, 200 * 1024 * 1024)
    db = app.state.session_factory()
    try:
        report = plan_regroup(db, tmp_path / "Films", series)
        assert report.groups == ()
        assert len(report.loose) == 1
        group = report.loose[0]
        assert group.media_id is None
        assert group.loose is True
        assert [move.src for move in group.moves] == [str(release.resolve())]
        assert "très petite" not in (group.moves[0].warning or "")
        assert str(stray.resolve()) not in report.unmatched
        applied, rename_id, _after = apply_regroup(
            db, tmp_path / "Films", series, [], validated=True, include_loose=True
        )
        db.commit()
        assert applied is True
        assert rename_id is None
        assert kept.is_file()
        assert not release.exists()
        held = list((tmp_path / ".trash").rglob(release.name))
        assert len(held) == 1
    finally:
        db.close()


def test_regroup_trash_purge_after_respects_trash_retention_days(tmp_path, client, app):
    del client
    moment = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
    series = tmp_path / "tvshows"
    home = series / "Refuge Sorcellerie Démo (2024)" / "Season 01"
    kept = home / "Refuge Sorcellerie Démo (2024) - S01E01.mkv"
    _sparse(kept, 80 * 1024 * 1024)
    stray = series / "Refuge.Sorcellerie.Démo.S01E01.1080p.WEB"
    release = stray / "Refuge.Sorcellerie.Démo.S01E01.FiNAL.1080p.WEB.mkv"
    _sparse(release, 200 * 1024 * 1024)
    db = app.state.session_factory()
    try:
        row = db.get(Setting, "trash_retention_days")
        if row is None:
            db.add(Setting(key="trash_retention_days", value="3"))
        else:
            row.value = "3"
        db.commit()
        applied, _rename_id, _after = apply_regroup(
            db,
            tmp_path / "Films",
            series,
            [],
            validated=True,
            include_loose=True,
            now=moment,
            settings=app.state.settings,
        )
        db.commit()
        assert applied is True
        entry = db.query(TrashEntry).one()
        assert entry.purge_after - entry.deleted_at == timedelta(days=3)
    finally:
        db.close()
