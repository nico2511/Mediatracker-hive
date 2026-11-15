"""Liaison Wawacity auto : pas de chemin figé en repli ou avant sortie film."""

from datetime import date, datetime, timezone

from mediatracker.core.film_availability import film_not_yet_available
from mediatracker.core.source_binding import (
    bindings_need_search,
    clear_auto_binding_path,
    remember_binding_fields,
    should_persist_auto_binding,
)
from mediatracker.core.source_poll import _remember_page
from mediatracker.db.models import Media, MediaItem, Provider, SourceBinding
from mediatracker.providers.base import SourceResult

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def _film(*, next_air: date | None = None) -> Media:
    return Media(
        id=1,
        type="film",
        title="Oeuvre Fictive",
        year=2099,
        new_episode_policy="auto",
        next_air_date=next_air,
    )


def test_should_persist_rejects_repli_profile_rank_for_film_only():
    film = _film(next_air=date(2020, 1, 1))
    assert should_persist_auto_binding(film, [], profile_rank=2) is False
    assert should_persist_auto_binding(film, [], profile_rank=1) is True
    series = Media(
        id=2,
        type="series",
        title="Série Fictive",
        year=2020,
        new_episode_policy="auto",
    )
    assert should_persist_auto_binding(series, [], profile_rank=2) is True


def test_should_persist_rejects_film_not_yet_available():
    media = _film(next_air=date(2099, 12, 1))
    items = [
        MediaItem(
            id=10,
            media_id=1,
            season=None,
            episode=None,
            downloaded=False,
            watched=False,
        )
    ]
    assert film_not_yet_available(media, items, today=date(2026, 11, 15))
    assert should_persist_auto_binding(media, items, profile_rank=1) is False


def test_remember_page_skips_auto_binding_on_repli_profile(client, app):
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        media = Media(
            type="film",
            title="Oeuvre Fictive",
            year=2099,
            new_episode_policy="auto",
            next_air_date=date(2020, 1, 1),
        )
        db.add(media)
        db.commit()
        page = SourceResult(
            title="Oeuvre Fictive",
            year=2099,
            media_type="film",
            ref="https://fiches.example/?p=film&id=vfq-auto",
            url="https://fiches.example/?p=film&id=vfq-auto",
            quality="1080p",
            language="MULTI",
            profile_hint="MULTI (VFQ)",
        )
        _remember_page(
            db,
            media.id,
            provider_id=provider.id,
            page=page,
            now=NOW,
            media=media,
            items=[],
            profile_rank=2,
        )
        db.commit()
        row = db.query(SourceBinding).filter(SourceBinding.media_id == media.id).one_or_none()
        assert row is None or not (row.path or row.url)
        assert bindings_need_search(db, media, provider_id=provider.id, seasons={None})
    finally:
        db.close()


def test_remember_page_stores_series_binding_on_repli_profile(client, app):
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        media = Media(
            type="series",
            title="Série Fictive",
            year=2020,
            new_episode_policy="auto",
        )
        db.add(media)
        db.commit()
        page = SourceResult(
            title="Série Fictive",
            year=2020,
            media_type="series",
            ref="https://fiches.example/?p=serie&id=saison1-vostfr",
            url="https://fiches.example/?p=serie&id=saison1-vostfr",
            quality="1080p",
            language="VOSTFR",
            season=1,
        )
        _remember_page(
            db,
            media.id,
            provider_id=provider.id,
            page=page,
            now=NOW,
            media=media,
            items=[],
            profile_rank=2,
        )
        db.commit()
        row = db.query(SourceBinding).filter(SourceBinding.media_id == media.id).one()
        assert row.path
        assert row.season == 1
        assert not bindings_need_search(db, media, provider_id=provider.id, seasons={1})
    finally:
        db.close()


def test_remember_page_does_not_overwrite_manual_path(client, app):
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        media = Media(
            type="film",
            title="Oeuvre Fictive",
            year=2099,
            new_episode_policy="auto",
            next_air_date=date(2020, 1, 1),
        )
        db.add(media)
        db.commit()
        manual_path = "/?p=film&id=manual-fiche"
        row = SourceBinding(
            media_id=media.id,
            provider_id=provider.id,
            external_ref="manual",
            url=f"https://fiches.example{manual_path}",
            path=manual_path,
            path_manual=True,
            active=True,
            season=None,
        )
        db.add(row)
        db.commit()
        page = SourceResult(
            title="Oeuvre Fictive",
            year=2099,
            media_type="film",
            ref="https://fiches.example/?p=film&id=vfq-auto",
            url="https://fiches.example/?p=film&id=vfq-auto",
            quality="1080p",
            language="MULTI",
        )
        _remember_page(
            db,
            media.id,
            provider_id=provider.id,
            page=page,
            now=NOW,
            media=media,
            items=[],
            profile_rank=2,
        )
        db.commit()
        db.refresh(row)
        assert row.path == manual_path
        assert row.path_manual is True
        assert row.last_scanned_at is not None
    finally:
        db.close()


def test_clear_auto_binding_path_keeps_manual():
    row = SourceBinding(path="/auto", url="https://fiches.example/auto", path_manual=False, active=True)
    clear_auto_binding_path(row)
    assert row.path is None and row.url is None
    manual = SourceBinding(path="/keep", url="https://fiches.example/keep", path_manual=True, active=True)
    clear_auto_binding_path(manual)
    assert manual.path == "/keep"
    remember_binding_fields(manual, "https://fiches.example/other")
    assert manual.path == "/keep"
