"""Release-year parsing: numeric show titles vs release years."""

import pytest

from mediatracker.library.audit import _same_show, audit_library
from mediatracker.library.parse import parse_path, parse_release, show_key


@pytest.fixture
def year_ceiling_2027(monkeypatch):
    monkeypatch.setattr("mediatracker.library.parse._release_year_ceiling", lambda: 2027)


def test_parse_release_future_bare_year_stays_in_title(year_ceiling_2027):
    """2099 in the show name must not become parsed_year."""
    parsed = parse_release("Serie Exemple 2099 - S01E01.mkv")
    assert parsed.title == "Serie Exemple 2099"
    assert parsed.year is None


def test_parse_release_parenthetical_year_overrides_bare_number(year_ceiling_2027):
    parsed = parse_release("Serie Exemple 2099 (2026) - S01E01.mkv")
    assert parsed.title == "Serie Exemple 2099"
    assert parsed.year == 2026


def test_parse_release_parenthetical_year_beyond_bare_ceiling(year_ceiling_2027):
    parsed = parse_release("Serie Exemple 2099 (2028) - S01E01.mkv")
    assert parsed.title == "Serie Exemple 2099"
    assert parsed.year == 2028


def test_parse_release_leading_bare_year(year_ceiling_2027):
    parsed = parse_release("2021 Serie Exemple S01E01 1080p.mkv")
    assert parsed.title == "Serie Exemple"
    assert parsed.year == 2021


def test_parse_release_adjacent_years_last_plausible_wins(year_ceiling_2027):
    parsed = parse_release("Serie Exemple 2099.2026 - S01E01.mkv")
    assert parsed.title == "Serie Exemple 2099"
    assert parsed.year == 2026


def test_parse_release_classic_bare_year_unchanged(year_ceiling_2027):
    parsed = parse_release("Serie Exemple 2021 S01E01 1080p.mkv")
    assert parsed.title == "Serie Exemple"
    assert parsed.year == 2021


def test_parse_release_film_bare_year_unchanged(year_ceiling_2027):
    parsed = parse_release("Film Exemple 2019 1080p.mkv")
    assert parsed.title == "Film Exemple"
    assert parsed.year == 2019


def test_show_key_keeps_future_number_in_title(year_ceiling_2027):
    assert show_key("Serie Exemple 2099 (2026)") == "serie exemple 2099"
    assert show_key("Serie Exemple 2099 - S01E01") == "serie exemple 2099"


def test_same_show_numeric_title_with_folder_year(year_ceiling_2027):
    """Résidu volontaire : _same_show accepte fichier « Serie Exemple » + année 1999
    face au dossier « Serie Exemple 1999 » quand 1999 est encore extraite du stem
    comme année plausible, au lieu d'exiger le titre complet dans parse_release."""
    parsed = parse_release("Serie Exemple 1999 - S01E01.mkv")
    assert parsed.title == "Serie Exemple"
    assert parsed.year == 1999
    assert _same_show(parsed.title, parsed.year, "Serie Exemple 1999", 2026)


def test_same_show_rejects_unrelated_titles(year_ceiling_2027):
    assert not _same_show("Serie Alpha", 2021, "Serie Beta", 2021)


def test_parse_path_inherits_folder_tmdb_and_title(year_ceiling_2027, tmp_path):
    root = tmp_path / "TV Shows"
    folder = root / "Serie Exemple 2099 (2026) [tmdbid-880001]"
    episode = folder / "Season 01" / "Serie Exemple 2099 - S01E01.mkv"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"")

    parsed = parse_path(episode, root=root, root_kind="series")
    assert parsed.tmdb_id == 880001
    assert parsed.title == "Serie Exemple 2099"
    assert parsed.year == 2026


def test_reproduction_audit_mismatch_avoided(tmp_path, year_ceiling_2027):
    """Folder Jellyfin + épisode nommé comme la série, pas « Mauvaise série »."""
    series_root = tmp_path / "TV Shows"
    folder = series_root / "Serie Exemple 2099 (2026)"
    season = folder / "Season 01"
    season.mkdir(parents=True)
    episode = season / "Serie Exemple 2099 - S01E01.mkv"
    episode.write_bytes(b"x" * 1024)

    report = audit_library(tmp_path / "Movies", series_root)
    mismatches = [row for row in report.items if row.kind == "mismatch"]
    assert mismatches == [], f"unexpected mismatches: {mismatches}"


def test_plausible_release_year_cutoff_matches_ceiling(monkeypatch):
    from mediatracker.library import parse as parse_mod

    monkeypatch.setattr(parse_mod, "_release_year_ceiling", lambda: 2027)
    assert parse_mod._plausible_release_year(2027) is True
    assert parse_mod._plausible_release_year(2028) is False