"""Réglages en base (délai sources, scan) primordiaux sur les défauts d'environnement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from tests.conftest import connect_alldebrid_for_tests

from mediatracker.core.source_force import _advance_past_wave
from mediatracker.db.models import Media, Setting
from mediatracker.db.session import session_scope
from mediatracker.providers.sources.loading import _int_setting, close_source, episode_source
from mediatracker.providers.sources.registry import all_sources
from mediatracker.providers.sources.stremio_addons import StremioAddonEntry
from mediatracker.providers.sources.uwuplus import UwuPlusProvider

COMET = "https://comet-fictif.example"
TORRENTIO = "https://torrentio-fictif.example"
STREMIO = "https://addon-fictif.example/manifest.json"
UWU = "https://uwu-fictif.example"

DB_DELAY = 30
ENV_DELAY = 5
DB_SCAN_MIN = 180
ENV_SCAN_MIN = 600

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _write_int_setting(app, key: str, value: int) -> None:
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, key)
        assert row is not None
        row.value = str(value)
        db.commit()


@pytest.fixture
def alldebrid_ready(client, app):
    del client  # initialise la base via le lifespan TestClient
    connect_alldebrid_for_tests(app)
    app.state.settings = app.state.settings.model_copy(
        update={
            "source_request_delay_seconds": ENV_DELAY,
            "scan_interval_min": ENV_SCAN_MIN,
        }
    )
    _write_int_setting(app, "source_request_delay_seconds", DB_DELAY)
    return app


@pytest.mark.parametrize(
    ("name", "kwargs", "delay_attr"),
    [
        ("comet", {"base_urls": [COMET]}, "_delay"),
        ("torrentio", {"base_url": TORRENTIO}, "pacer"),
        (
            "stremio_addons",
            {"addons": [StremioAddonEntry(url=STREMIO, enabled=True)]},
            "_delay",
        ),
    ],
)
def test_episode_source_uses_db_request_delay(alldebrid_ready, name, kwargs, delay_attr):
    app = alldebrid_ready
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        source = episode_source(name, settings, db=db, **kwargs)
    assert source is not None
    try:
        value = getattr(source, delay_attr)
        if delay_attr == "pacer":
            assert value.delay == float(DB_DELAY)
        else:
            assert value == float(DB_DELAY)
    finally:
        close_source(source)


def test_uwuplus_open_episode_uses_passed_db_delay(alldebrid_ready):
    app = alldebrid_ready
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
        source = UwuPlusProvider.open_episode(
            settings,
            base_url=UWU,
            request_delay_seconds=delay,
        )
    assert source is not None
    try:
        assert source.pacer.delay == float(DB_DELAY)
        assert source.pacer.delay != float(ENV_DELAY)
    finally:
        source.close()


def _episode_source_kwargs(name: str) -> dict:
    if name == "comet":
        return {"base_urls": [COMET]}
    if name == "torrentio":
        return {"base_url": TORRENTIO}
    if name == "stremio_addons":
        return {"addons": [StremioAddonEntry(url=STREMIO, enabled=True)]}
    return {"base_url": "https://source-fictive.example"}


def test_episode_source_accepts_delay_for_every_episode_registry_entry(alldebrid_ready, monkeypatch):
    """Aucune entrée « episode » du registre ne doit lever TypeError sur request_delay_seconds."""
    app = alldebrid_ready
    settings = app.state.settings
    monkeypatch.setattr(
        "mediatracker.core.source_availability.source_usable",
        lambda db, settings, name: True,
    )
    episode_names = [entry.name for entry in all_sources() if "episode" in entry.capabilities]
    assert episode_names, "registre attendu avec au moins une capacité episode"
    with session_scope(app.state.session_factory) as db:
        for name in episode_names:
            source = episode_source(name, settings, db=db, **_episode_source_kwargs(name))
            if source is not None:
                close_source(source)


def test_forced_wave_advance_uses_db_scan_interval(alldebrid_ready):
    app = alldebrid_ready
    settings = app.state.settings
    _write_int_setting(app, "scan_interval_min", DB_SCAN_MIN)
    since = NOW - timedelta(minutes=5)
    with session_scope(app.state.session_factory) as db:
        media = Media(
            type="series",
            title="Fiche-Démo-Intervalle",
            year=2099,
            followed=True,
            on_hold=False,
            new_episode_policy="auto",
            scan_interval_min=None,
            next_source_poll_at=NOW - timedelta(minutes=1),
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        _advance_past_wave(db, media, since, settings, commit=True)
        db.refresh(media)
        nxt = media.next_source_poll_at
        assert nxt is not None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        after_wave = datetime.now(timezone.utc)
        delta_min = (nxt - after_wave).total_seconds() / 60.0
        assert DB_SCAN_MIN - 1 < delta_min < DB_SCAN_MIN + 2
        assert delta_min < ENV_SCAN_MIN - 60