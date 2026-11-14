"""Home Assistant follow queue: resultat defaults, and ids stay pending without a confirmation."""

import re
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined, UndefinedError

_ROOT = Path(__file__).resolve().parents[1] / "homeassistant"


def _package() -> str:
    return (_ROOT / "mediatracker.yaml").read_text(encoding="utf-8")


def _snippet(name: str) -> str:
    text = _package()
    begin = f"{{# BEGIN {name} #}}"
    end = f"{{# END {name} #}}"
    return text[text.index(begin) : text.index(end, text.index(begin))]


def _env() -> Environment:
    env = Environment(undefined=StrictUndefined, autoescape=False)
    env.filters["trim"] = lambda value: "" if value is None else str(value).strip()
    return env


def _render(name: str, **kwargs: object) -> str:
    return _env().from_string(_snippet(name)).render(**kwargs)


def test_reading_resultat_content_before_a_default_is_the_reported_error():
    template = _env().from_string("{% set body = resultat.content | default(none) %}{{ body }}")
    with pytest.raises(UndefinedError, match="resultat"):
        template.render()


def test_follow_message_defaults_resultat_and_says_when_mediatracker_is_silent():
    package = _package()
    assert "resultat.content" not in package
    assert package.count("resultat | default(none)") >= 3
    assert _render("follow_message") == "MediaTracker ne répond pas. L'identifiant reste en attente."
    assert _render("follow_verdict") == "pending"
    assert _render("follow_message", resultat=None) == "MediaTracker ne répond pas. L'identifiant reste en attente."
    assert _render("follow_message", resultat={"status": 0}) == (
        "MediaTracker ne répond pas. L'identifiant reste en attente."
    )
    assert _render("follow_verdict", resultat={"status": 503, "content": None}) == "pending"
    assert _render("update_message") == "Vérification impossible. MediaTracker ne répond pas."


def test_follow_message_keeps_a_refused_id_and_confirms_a_real_body():
    refused = _render("follow_message", resultat={"status": 422, "content": {"detail": "Identifiant inconnu"}})
    assert refused == "Identifiant inconnu. L'identifiant reste en attente."
    refused_verdict = _render(
        "follow_verdict",
        resultat={"status": 422, "content": {"detail": "Identifiant inconnu"}},
    )
    assert refused_verdict == "refused"
    assert _render(
        "follow_message",
        resultat={"status": 200, "content": {"ok": True, "detail": "Orbite Démo est suivi."}},
    ) == "Orbite Démo est suivi."
    assert _render(
        "follow_verdict",
        resultat={"status": 200, "content": {"ok": True, "detail": "Orbite Démo est suivi."}},
    ) == "confirmed"
    assert _render("follow_message", resultat={"status": 200, "content": None}) == (
        "Réponse vide. L'identifiant reste en attente."
    )
    assert _render("follow_verdict", resultat={"status": 200, "content": None}) == "pending"
    updated = _render("update_message", resultat={"status": 200, "content": {"detail": "Image à jour."}})
    assert updated == "Image à jour."


def test_empty_tmdb_field_is_rejected_before_the_request():
    package = _package()
    block = package.split("alias: MediaTracker — suivre\n", 1)[1].split("\n  mediatracker_", 1)[0]
    assert "Indiquez un identifiant TMDB." in block
    assert block.index("Indiquez un identifiant TMDB.") < block.index("script.mediatracker_envoyer")
    assert "rest_command." not in block
    check = _env().from_string("{{ saisi | trim | int(0) < 1 }}")
    assert check.render(saisi="") == "True"
    assert check.render(saisi="  ") == "True"
    assert check.render(saisi="abc") == "True"
    assert check.render(saisi="901001") == "False"


def test_queue_keeps_an_id_until_it_is_confirmed_and_caps_the_list():
    assert _render("queue_upsert", raw="", id="901001", kind="t", status="a") == "901001:t:a"
    assert (
        _render("queue_upsert", raw="901001:t:a,901003:m:a", id="901001", kind="t", status="e")
        == "901001:t:e,901003:m:a"
    )
    assert _render("queue_upsert", raw="901001:t:h", id="901001", kind="t", status="a") == "901001:t:a"
    assert _render("queue_rows", raw="nope,12:x:a,8:m:a") == "8:m:a"
    assert _render("queue_next", raw="901001:t:h,901002:m:e,901003:m:a") == "901003:m"
    assert _render("queue_next", raw="901001:t:e,901003:m:h") == ""
    full = ",".join(f"{i}:m:a" for i in range(1, 13))
    stored = _render("queue_upsert", raw=full, id="99", kind="t", status="a")
    parts = stored.split(",")
    assert len(parts) == 12
    assert parts[-1] == "99:t:a"
    assert "1:m:a" not in parts
    assert "2:m:a" in parts


def test_queue_status_letter_matches_the_verdict():
    package = _package()
    line = "{{ 'e' if verdict | trim == 'confirmed' else ('h' if verdict | trim == 'refused' else 'a') }}"
    assert line in package
    render = _env().from_string(line).render
    assert render(verdict="pending") == "a"
    assert render(verdict="confirmed") == "e"
    assert render(verdict="refused") == "h"


def test_queue_rows_feed_attente_and_envoye_slots():
    class Loader(yaml.SafeLoader):
        pass

    def secret(loader, node):
        return loader.construct_scalar(node)

    Loader.add_constructor("!secret", secret)
    data = yaml.load(_package(), Loader=Loader)
    sensors = {row["unique_id"]: row for row in data["template"][0]["sensor"]}
    assert set(sensors) >= {f"mediatracker_attente_{i:02d}" for i in range(1, 13)}
    rows = "901001:t:a,901003:m:h,901004:t:e"

    def state_attr(_entity, _attr):
        return rows

    env = _env()
    env.globals["state_attr"] = state_attr
    assert env.from_string(sensors["mediatracker_attente_01"]["state"]).render().strip() == "en attente"
    assert env.from_string(sensors["mediatracker_attente_02"]["state"]).render().strip() == "en attente"
    assert env.from_string(sensors["mediatracker_attente_03"]["state"]).render().strip() == "envoyé"
    assert env.from_string(sensors["mediatracker_attente_04"]["state"]).render().strip() == "vide"
    assert "input_text.mediatracker_file_attente" in _package()
    assert "mediatracker_envoyer_file" in _package()
    assert re.search(r"mt_[A-Za-z0-9]{8,}", _package()) is None
    assert re.search(r"mt_[A-Za-z0-9]{8,}", (_ROOT / "dashboard.yaml").read_text(encoding="utf-8")) is None
