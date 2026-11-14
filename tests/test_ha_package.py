"""The Home Assistant package names the MediaTracker host in one place."""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1] / "homeassistant"
_HOST = "mediatracker.example.com"


def test_mediatracker_host_is_written_once():
    hits = []
    for path in sorted(_ROOT.rglob("*")):
        if path.suffix not in {".yaml", ".yml", ".md"} or not path.is_file():
            continue
        if _HOST in path.read_text(encoding="utf-8"):
            hits.append(path.name)
    assert hits == ["secrets.yaml"]


def test_package_reuses_the_address_sensor():
    package = (_ROOT / "mediatracker.yaml").read_text(encoding="utf-8")
    dashboard = (_ROOT / "dashboard.yaml").read_text(encoding="utf-8")
    secrets = (_ROOT / "secrets.yaml").read_text(encoding="utf-8")
    assert "mediatracker_url:" in secrets
    assert package.count("!secret mediatracker_url") == 1
    assert "sensor.mediatracker_adresse" in package
    assert "unique_id: mediatracker_version" in package
    assert package.count("resource_template:") == 1
    assert "http://" not in package.split("state: !secret mediatracker_url", 1)[-1]
    assert "sensor.mediatracker_adresse" in dashboard
    assert "iframe" not in dashboard or "sensor.mediatracker_adresse" in dashboard


def test_sorties_skip_empty_posters_and_the_tmdb_field_starts_blank():
    package = (_ROOT / "mediatracker.yaml").read_text(encoding="utf-8")
    dashboard = (_ROOT / "dashboard.yaml").read_text(encoding="utf-8")
    field = package.split("mediatracker_tmdb_id:", 1)[1].split("input_select:", 1)[0]
    assert 'initial: ""' in field
    assert "min: 0" in field
    assert "type: picture-entity" not in dashboard
    assert "custom:mushroom-entity-card" in dashboard
    assert "custom:button-card" in dashboard
    assert "custom:auto-entities" in dashboard
    assert "custom:layout-card" in dashboard
    assert "card_mod:" in dashboard
    assert "File d'attente (NAS éteint)" in dashboard
    assert "repeat(auto-fill, 76px)" in dashboard
    assert "place-content: start" in dashboard
    view = yaml.safe_load(dashboard)["views"][0]
    assert view["type"] == "custom:grid-layout"
    layout = view["layout"]
    assert layout["grid-template-columns"] == "minmax(0, 1fr) minmax(0, 1.15fr) minmax(0, 1fr)"
    assert "status downloads watch" in layout["grid-template-areas"]
    assert "sorties sorties suivre" in layout["grid-template-areas"]
    assert "antenne antenne queue" in layout["grid-template-areas"]
    mobile = layout["mediaquery"]["(max-width: 839px)"]
    assert mobile["grid-template-columns"] == "100%"
    assert "status downloads" not in mobile["grid-template-areas"]
    assert '"status"' in mobile["grid-template-areas"]
    areas = [card["view_layout"]["grid-area"] for card in view["cards"]]
    assert areas == ["status", "downloads", "watch", "sorties", "antenne", "suivre", "queue"]
    assert "https?:\\/" in dashboard
    for card in view["cards"]:
        if card["view_layout"]["grid-area"] not in {"sorties", "antenne"}:
            continue
        posters = card["card"]["cards"][1]
        assert posters["show_empty"] is False
        states = {row["state"] for row in posters["filter"]["exclude"]}
        assert {"—", "unknown", "unavailable", ""} <= states
    assert "poster_url if row is mapping else" not in package
    assert package.count("is match('^https?://')") >= 12
