"""Catalogue : fusion des titres « - FRENCH » et parse_heading."""

from datetime import datetime, timezone

from sqlalchemy import select

from mediatracker.core.catalog import dedupe_key
from mediatracker.core.catalog_french_merge import (
    _MIGRATION_KEY,
    migrate_catalog_french_duplicate_titles,
    run_catalog_french_migration_safe,
)
from mediatracker.db.models import CatalogEntry, Setting
from mediatracker.providers.sources.wawacity_html import parse_heading

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def _clear_french_flag(db) -> None:
    prior = db.get(Setting, _MIGRATION_KEY)
    if prior is not None:
        db.delete(prior)
        db.commit()


def test_parse_heading_strips_french_suffix():
    heading = parse_heading("Exemple Titre - FRENCH [HD] (2025)")
    assert heading.title == "Exemple Titre"


def test_migrate_merges_french_duplicate_into_canonical(client, app):
    del client
    db = app.state.session_factory()
    try:
        canonical = CatalogEntry(
            dedupe_key=dedupe_key("Exemple Titre", 2025, "film", None, None),
            title="Exemple Titre",
            title_folded="exemple titre",
            year=2025,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/exemple",
            poster_url="https://image.tmdb.org/t/p/w500/exemple.jpg",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        duplicate = CatalogEntry(
            dedupe_key=dedupe_key("Exemple Titre - FRENCH", 2025, "film", None, None),
            title="Exemple Titre - FRENCH",
            title_folded="exemple titre french",
            year=2025,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/exemple-fr",
            source_poster_url="/img/films/exemple.webp",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(canonical)
        db.add(duplicate)
        db.commit()
        dup_id = duplicate.id
        _clear_french_flag(db)

        first = migrate_catalog_french_duplicate_titles(db)
        db.commit()
        assert first == 1
        assert db.get(CatalogEntry, dup_id) is None
        kept = db.scalar(select(CatalogEntry).where(CatalogEntry.title == "Exemple Titre"))
        assert kept is not None
        assert kept.source_poster_url == "/img/films/exemple.webp"

        second = migrate_catalog_french_duplicate_titles(db)
        assert second == 0
    finally:
        db.close()


def test_migrate_merges_two_french_variants_without_canonical_row(client, app):
    del client
    db = app.state.session_factory()
    try:
        first = CatalogEntry(
            dedupe_key=dedupe_key("Exemple Alpha - FRENCH", 2024, "film", None, None),
            title="Exemple Alpha - FRENCH",
            title_folded="exemple alpha french",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/a-fr",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        second = CatalogEntry(
            dedupe_key=dedupe_key("Exemple Alpha - MULTI - FRENCH", 2024, "film", None, None),
            title="Exemple Alpha - MULTI - FRENCH",
            title_folded="exemple alpha multi french",
            year=2024,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/a-multi-fr",
            source_poster_url="/img/films/a.webp",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(first)
        db.add(second)
        db.commit()
        second_id = second.id
        _clear_french_flag(db)

        touched = migrate_catalog_french_duplicate_titles(db)
        db.commit()
        assert touched == 2
        rows = list(db.scalars(select(CatalogEntry).where(CatalogEntry.year == 2024)).all())
        assert len(rows) == 1
        assert rows[0].title == "Exemple Alpha"
        assert rows[0].source_poster_url == "/img/films/a.webp"
        assert db.get(CatalogEntry, second_id) is None
    finally:
        db.close()


def test_safe_migration_does_not_raise_on_integrity_error(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    try:
        _clear_french_flag(db)

        def _boom(_db):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(
            "mediatracker.core.catalog_french_merge.migrate_catalog_french_duplicate_titles",
            _boom,
        )
        assert run_catalog_french_migration_safe(db) == 0
        assert db.get(Setting, _MIGRATION_KEY) is None
    finally:
        db.close()


def test_safe_migration_rolls_back_row_changes(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    try:
        row = CatalogEntry(
            dedupe_key=dedupe_key("Exemple Rollback - FRENCH", 2023, "film", None, None),
            title="Exemple Rollback - FRENCH",
            title_folded="exemple rollback french",
            year=2023,
            media_type="film",
            source="wawacity",
            source_url="https://wawa.test/film/rollback",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
        )
        db.add(row)
        db.commit()
        row_id = row.id
        original_title = row.title
        _clear_french_flag(db)

        def _fail_mid(session):
            entry = session.get(CatalogEntry, row_id)
            assert entry is not None
            entry.title = "Titre modifié puis annulé"
            session.flush()
            raise RuntimeError("forced failure")

        monkeypatch.setattr(
            "mediatracker.core.catalog_french_merge.migrate_catalog_french_duplicate_titles",
            _fail_mid,
        )
        assert run_catalog_french_migration_safe(db) == 0
        db.expire_all()
        refreshed = db.get(CatalogEntry, row_id)
        assert refreshed is not None
        assert refreshed.title == original_title
        assert db.get(Setting, _MIGRATION_KEY) is None
    finally:
        db.close()
