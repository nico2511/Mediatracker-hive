"""Sans AllDebrid : pas d'affichage des flux Wawacity (catalogue, fiche, HA)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from tests.conftest import connect_alldebrid_for_tests, csrf, login

from mediatracker.db.models import CatalogEntry, Link, Media, MediaItem, Provider


def _seed_wawacity_catalog(db, *, title: str, media_type: str, dedupe: str) -> None:
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    db.add(
        CatalogEntry(
            dedupe_key=dedupe,
            title=title,
            year=2025,
            media_type=media_type,
            quality="HD",
            language="VFF",
            source="wawacity",
            source_url="https://example.invalid/catalog",
            sources=[],
            first_seen_at=now,
            last_seen_at=now,
            title_folded=title.lower(),
            tmdb_id=9001 if media_type == "film" else 9002,
        )
    )


def test_catalog_api_empty_without_alldebrid(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        _seed_wawacity_catalog(db, title="Film W", media_type="film", dedupe="w-film|2025|film||")
        _seed_wawacity_catalog(db, title="Serie W", media_type="series", dedupe="w-ser|2025|series||")
        db.commit()
    finally:
        db.close()
    status = client.get("/api/v1/catalog/status", headers=csrf(client)).json()
    assert status["news_sources_usable"] is False
    assert status["notice"] == "Aucune source de nouveautés utilisable"
    films = client.get("/api/v1/catalog?type=film", headers=csrf(client)).json()
    assert films["items"] == []
    assert films["total"] == 0
    assert films["notice"] == "Aucune source de nouveautés utilisable"


def test_catalog_api_with_alldebrid_unchanged(client, app):
    login(client)
    connect_alldebrid_for_tests(app)
    db = app.state.session_factory()
    try:
        _seed_wawacity_catalog(db, title="Film W", media_type="film", dedupe="w-film2|2025|film||")
        db.commit()
    finally:
        db.close()
    status = client.get("/api/v1/catalog/status", headers=csrf(client)).json()
    assert status["news_sources_usable"] is True
    page = client.get("/api/v1/catalog?type=film", headers=csrf(client)).json()
    assert page["total"] >= 1
    assert any(item["title"] == "Film W" for item in page["items"])


def test_media_hides_wawacity_catalog_link_without_ad(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Fiche W", year=2025, tmdb_id=9010, followed=True)
        db.add(media)
        db.flush()
        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        db.add(
            CatalogEntry(
                dedupe_key="w-f3|2025|film||",
                title="Film W",
                year=2025,
                media_type="film",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://example.invalid/catalog",
                sources=[],
                first_seen_at=now,
                last_seen_at=now,
                title_folded="film w",
                tmdb_id=9010,
            )
        )
        db.commit()
        mid = media.id
    finally:
        db.close()
    detail = client.get(f"/api/v1/media/{mid}", headers=csrf(client)).json()
    assert detail.get("catalog_id") is None
    assert detail.get("source_url") is None


def test_dead_wawacity_link_hidden_without_ad(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        w_provider = db.scalar(select(Provider).where(Provider.type == "wawacity").limit(1))
        assert w_provider is not None
        media = Media(type="film", title="Liens W", year=2025, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        db.add(
            Link(
                media_item_id=item.id,
                provider_id=w_provider.id,
                kind="hoster",
                url="https://example.invalid/dead",
                hoster="uptobox",
                external_id="dead-1",
                dead=True,
                last_error="hors ligne",
            )
        )
        db.commit()
        mid = media.id
    finally:
        db.close()
    rows = client.get(f"/api/v1/media/{mid}/dead-links", headers=csrf(client)).json()
    assert rows == []
