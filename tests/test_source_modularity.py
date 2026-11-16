"""Download sources come from the registry. Existing URL settings are kept."""

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.bootstrap import ensure_providers
from mediatracker.core.pipeline import unlock
from mediatracker.core.selection import parse_download_source_order, registered_download_sources
from mediatracker.db.models import Provider, Setting
from mediatracker.providers.sources.loading import source_base_url


def test_registry_accepts_the_builtin_pair_and_refuses_an_unknown_name():
    assert registered_download_sources() == (
        "wawacity",
        "free_telecharger",
        "hive",
        "comet",
        "torrentio",
        "stremio_addons",
    )
    assert parse_download_source_order("wawacity, comet") == ["wawacity", "comet"]
    assert parse_download_source_order("comet,wawacity,torrentio") == ["comet", "wawacity", "torrentio"]
    try:
        parse_download_source_order("unknown-source")
    except ValueError as exc:
        assert "inconnue" in str(exc)
    else:
        raise AssertionError("unknown source should be refused")


def test_download_follows_capability_not_the_name(monkeypatch):
    seen: list[str] = []

    def hosters(*_args, **_kwargs):
        seen.append("links")
        return "hoster"

    def episode(*_args, **kwargs):
        seen.append(kwargs.get("source_type") or "")
        return "episode"

    monkeypatch.setattr(unlock, "_from_hosters", hosters)
    monkeypatch.setattr(unlock, "_from_episode_source", episode)
    monkeypatch.setattr(unlock, "_from_hive", lambda *a, **k: None)

    def call(name: str):
        return unlock._from_named_source(
            None,
            name,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            defer_final=False,
        )

    assert call("wawacity") == "hoster"
    assert call("comet") == "episode"
    assert seen == ["links", "comet"]
    assert call("torrentio") == "episode"
    assert seen == ["links", "comet", "torrentio"]
    assert call("manual") is None
    assert call("hive") is None


def test_provider_types_list_download_sources(client):
    login(client)
    items = client.get("/api/v1/providers/types?kind=source").json()["items"]
    by_type = {item["type"]: item for item in items}
    assert by_type["wawacity"]["download_rank"] == 10
    assert "links" in by_type["wawacity"]["capabilities"]
    assert "base_url" in by_type["wawacity"]["config_schema"]["properties"]
    assert by_type["comet"]["download_rank"] == 20
    assert "episode" in by_type["comet"]["capabilities"]
    assert "base_url" in by_type["comet"]["config_schema"]["properties"]
    assert by_type["torrentio"]["download_rank"] == 21
    assert "episode" in by_type["torrentio"]["capabilities"]
    assert "base_url" in by_type["torrentio"]["config_schema"]["properties"]
    assert by_type["stremio_addons"]["download_rank"] == 23
    assert "episode" in by_type["stremio_addons"]["capabilities"]
    assert "addons" in by_type["stremio_addons"]["config_schema"]["properties"]
    assert by_type["manual"]["download_rank"] is None


def test_saving_a_source_url_keeps_the_setting_and_fills_provider_config(client):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "wawacity_base_url": "https://wawa.example",
                "comet_base_url": "https://comet.example",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    rows = {row["key"]: row["value"] for row in saved.json()["settings"]}
    assert rows["wawacity_base_url"] == "https://wawa.example"
    assert rows["comet_base_url"] == "https://comet.example"
    providers = {item["type"]: item for item in client.get("/api/v1/providers?kind=source").json()["items"]}
    assert providers["wawacity"]["config"]["base_url"] == "https://wawa.example"
    assert providers["comet"]["config"]["base_url"] == "https://comet.example"


def test_boot_copies_a_legacy_url_without_erasing_it_or_a_config_already_set(client, app):
    del client
    db = app.state.session_factory()
    try:
        wawacity = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "wawacity"))
        comet = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "comet"))
        assert wawacity is not None and comet is not None
        wawacity.config = {}
        comet.config = {"base_url": "https://kept.example"}
        wawacity_setting = db.get(Setting, "wawacity_base_url")
        comet_setting = db.get(Setting, "comet_base_url")
        comet_urls_setting = db.get(Setting, "comet_base_urls")
        assert wawacity_setting is not None and comet_setting is not None
        wawacity_setting.value = "https://wawa.example/path"
        comet_setting.value = "https://old.example"
        if comet_urls_setting is not None:
            db.delete(comet_urls_setting)
        db.commit()
        ensure_providers(db, app.state.settings)
        db.refresh(wawacity)
        db.refresh(comet)
        db.refresh(wawacity_setting)
        db.refresh(comet_setting)
        assert wawacity.config["base_url"] == "https://wawa.example/path"
        assert wawacity_setting.value == "https://wawa.example/path"
        assert comet.config["base_url"] == "https://kept.example"
        assert comet_setting.value == "https://old.example"
        assert source_base_url(db, app.state.settings, "comet") == "https://kept.example"
        assert source_base_url(db, app.state.settings, "wawacity") == "https://wawa.example/path"
    finally:
        db.close()
