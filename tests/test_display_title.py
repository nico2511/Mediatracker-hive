"""Display titles drop empty parentheses and language junk without touching storage."""

from mediatracker.core.display_title import clean_display_title
from mediatracker.core.release_title import release_caption


def test_empty_parentheses_and_language_tags_disappear():
    assert clean_display_title("Archipel Pilote ()") == "Archipel Pilote"
    assert clean_display_title("Archipel Pilote ( )") == "Archipel Pilote"
    assert clean_display_title("Archipel Pilote (FRENCH)") == "Archipel Pilote"
    assert clean_display_title("Archipel Pilote [french]") == "Archipel Pilote"
    assert clean_display_title("The Show (VOSTFR) (1080p)") == "The Show"
    assert clean_display_title("Franchise Démo (FRENCH) ( )") == "Franchise Démo"
    assert clean_display_title("HD Forever") == "HD Forever"
    assert clean_display_title("Archipel Pilote (Director's Cut)") == "Archipel Pilote (Director's Cut)"
    assert clean_display_title("") == ""


def test_release_caption_uses_the_display_title():
    title, version = release_caption("The Show (FRENCH) - VF", language="VF")
    assert title == "The Show"
    assert version == "VF"
