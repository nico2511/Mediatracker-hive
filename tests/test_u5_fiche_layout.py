"""U5 fiche: settings at bottom, line actions in Plus d'actions menus."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "frontend" / "src" / "views" / "MediaDetailView.vue"
LINE_MENU = ROOT / "frontend" / "src" / "components" / "LineMoreActions.vue"
MAINTENANCE = ROOT / "frontend" / "src" / "components" / "FicheMaintenance.vue"
FILM_FILE = ROOT / "frontend" / "src" / "components" / "FilmFileBlock.vue"


def test_settings_block_is_after_seasons_in_template():
    source = DETAIL.read_text(encoding="utf-8")
    seasons = source.index("fiche-seasons-stack")
    settings = source.index("data-fiche-settings")
    assert seasons < settings


def test_settings_rows_show_summary_values():
    source = DETAIL.read_text(encoding="utf-8")
    maintenance = MAINTENANCE.read_text(encoding="utf-8")
    assert "sourceSettingSummary" in source
    assert "conservationSettingSummary" in source
    assert "identificationSettingSummary" in source
    assert "fiche-setting-summary" in source
    assert "maintenanceSettingSummary" in maintenance
    assert 'class="fiche-setting-summary"' in maintenance


def test_episode_and_season_actions_use_line_more_menu():
    source = DETAIL.read_text(encoding="utf-8")
    menu = LINE_MENU.read_text(encoding="utf-8")
    assert "LineMoreActions" in source
    assert "Plus d'actions" in menu
    assert "Forcer la saison" in source
    assert "Forcer le téléchargement" in source
    assert ':aria-expanded="seasonOpen[seasonStorageKey(group.number)]' in source


def test_garder_toggles_remain_on_fiche_season_and_episode():
    source = DETAIL.read_text(encoding="utf-8")
    film_file = FILM_FILE.read_text(encoding="utf-8")
    assert "Garder" in film_file and "(hors nettoyage)" in film_file
    assert "Garder la série" in source
    assert "setSeasonKeep" in source
    assert "setEpisodeKeep" in source
