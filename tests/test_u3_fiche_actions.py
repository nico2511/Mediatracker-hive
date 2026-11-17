"""U3 fiche: verify action stays reachable; unfollowed fiche can follow again."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ROOT / "frontend" / "src" / "components" / "FicheStateActions.vue"
DETAIL = ROOT / "frontend" / "src" / "views" / "MediaDetailView.vue"
DIALOG = ROOT / "frontend" / "src" / "components" / "DeleteFicheDialog.vue"


def test_more_actions_offers_verify_when_primary_is_not_check():
    source = ACTIONS.read_text(encoding="utf-8")
    assert "showCheckInMore" in source
    assert 'v-if="showCheckInMore"' in source
    assert ':disabled="checkDisabled"' in source
    assert "@click=\"emit('check')\"" in source
    assert "Vérifier maintenant" in source


def test_delete_fiche_dialog_bulk_labels():
    source = DIALOG.read_text(encoding="utf-8")
    assert "Supprimer les fiches" in source
    assert "Les fiches, leurs épisodes" in source
    assert "Les fichiers sur le disque ne sont pas touchés." in source
    assert "et {{ previewTitles.rest }} autre" in source


def test_fiche_details_offers_journal_link():
    source = DETAIL.read_text(encoding="utf-8")
    assert "Détails techniques" in source
    assert "Voir dans le journal" in source
    assert "media_id: String(media.id)" in source
