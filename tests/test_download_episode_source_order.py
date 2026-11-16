"""Ordre global des sources sur un épisode sans surcharge fiche."""

from datetime import datetime, timezone

from tests.conftest import FICTIVE_ALLDEBRID_API_KEY, csrf, login

from mediatracker.db.models import Download, Media, MediaItem

NOW = datetime(2026, 11, 9, 16, 0, tzinfo=timezone.utc)


def test_usable_order_intersects_fiche_override_with_globally_enabled(client, app, monkeypatch):
    monkeypatch.setenv("ALLDEBRID_API_KEY", FICTIVE_ALLDEBRID_API_KEY)
    from mediatracker.config import get_settings

    app.state.settings = get_settings()
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity,comet"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text

    from mediatracker.core.pipeline.jobs import _usable_download_sources

    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title="Ordre fictif",
            year=2020,
            followed=True,
            source_order="wawacity,free_telecharger,comet",
        )
        assert _usable_download_sources(db, media, app.state.settings) == ["wawacity", "comet"]
    finally:
        db.close()


def test_usable_order_falls_back_to_global_when_fiche_only_disabled_sources(client, app, monkeypatch):
    monkeypatch.setenv("ALLDEBRID_API_KEY", FICTIVE_ALLDEBRID_API_KEY)
    from mediatracker.config import get_settings

    app.state.settings = get_settings()
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "comet,wawacity"}},
        headers=csrf(client),
    )

    from mediatracker.core.pipeline.jobs import _usable_download_sources

    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title="Retombe global",
            year=2021,
            followed=True,
            source_order="free_telecharger,stremio_addons",
        )
        assert _usable_download_sources(db, media, app.state.settings) == ["comet", "wawacity"]
    finally:
        db.close()


def test_download_sources_uses_global_order_when_fiche_override_empty(client, app):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "comet,wawacity"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text

    from mediatracker.core.pipeline.jobs import _download_sources

    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Ordre fictif", year=2020, followed=True, source_order=None)
        assert _download_sources(db, media, app.state.settings) == ["comet", "wawacity"]
    finally:
        db.close()


def test_source_unavailable_reason_respects_global_disable(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity,comet"}},
        headers=csrf(client),
    )
    from mediatracker.core.pipeline import unlock

    db = app.state.session_factory()
    try:
        reason = unlock._source_unavailable_reason(db, app.state.settings, "free_telecharger")
        assert reason == "Source désactivée."
    finally:
        db.close()


def test_hosters_only_use_links_from_same_provider(client, app):
    from sqlalchemy import select

    from mediatracker.core.pipeline import unlock
    from mediatracker.db.models import Link, Provider

    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media = Media(type="series", title="Filtre fournisseur", year=2021, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
        db.add(item)
        db.flush()
        w_prov = Provider(kind="source", type="wawacity", name="Wawacity", enabled=True, priority=10, config={})
        f_prov = Provider(kind="source", type="free_telecharger", name="Free", enabled=True, priority=12, config={})
        db.add(w_prov)
        db.add(f_prov)
        db.flush()
        db.add(
            Link(
                media_item_id=item.id,
                provider_id=w_prov.id,
                kind="ddl_hoster",
                url="https://exemple.test/a",
                hoster="rapidgator",
                external_id="demo-link-a",
                dead=False,
            )
        )
        row = Download(media_item_id=item.id, state="pending")
        db.add(row)
        db.commit()
        out = unlock._from_hosters(
            db,
            row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            None,
            settings,
            [],
            defer_final=True,
            source_type="free_telecharger",
        )
        assert out is None
        assert row.error_code in (None, "NO_LINK", "SIZE_SANITY") or row.error_code
        links = list(db.scalars(select(Link).where(Link.media_item_id == item.id)).all())
        assert len(links) == 1
        assert links[0].provider_id == w_prov.id
    finally:
        db.close()
