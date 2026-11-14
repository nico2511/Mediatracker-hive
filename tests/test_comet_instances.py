"""Ordered Comet instances: settings, cascade, and keyless requests."""

import json
from pathlib import Path

import httpx
import pytest
from tests.conftest import csrf, login

from mediatracker.core.quality_profile import Profile
from mediatracker.db.models import Setting
from mediatracker.db.session import session_scope
from mediatracker.providers.base import EpisodeQuery, SourcePreferences
from mediatracker.providers.sources.comet import (
    CometCascadeSource,
    CometSource,
    normalize_comet_url,
)
from mediatracker.providers.sources.comet_urls import (
    DEFAULT_COMET_INSTANCE_URLS,
    default_comet_urls_from_legacy,
    format_comet_url_list,
    loads_comet_urls_from_api,
    normalize_comet_url_list,
    parse_comet_url_setting_text,
    resolve_comet_base_urls,
)
from mediatracker.settings_store import SettingsError, ensure_settings

FIXTURES = Path(__file__).parent / "fixtures" / "comet"
QUERY = EpisodeQuery(
    title="Clinique Démo",
    year=2025,
    media_type="series",
    imdb_id="tt0000002",
    season=1,
    episode=1,
)
PREFS = SourcePreferences(quality="HD", language="VFF", allow_fallback=False)
PRINCIPAL = Profile(
    name="Pilotage",
    languages=("VFF",),
    min_resolution="HD",
    max_resolution="1080p",
    min_size_mb=None,
    max_size_mb=None,
    exclude_keywords=(),
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_normalize_dedupes_and_strips_trailing_slash():
    urls = normalize_comet_url_list(
        [
            "https://a.example/",
            "https://b.example",
            "https://a.example",
            "HTTPS://A.EXAMPLE",
        ]
    )
    assert urls == ["https://a.example", "https://b.example"]


def test_migration_custom_legacy_first_idempotent(client, app):
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        legacy = db.get(Setting, "comet_base_url")
        listed = db.get(Setting, "comet_base_urls")
        assert legacy is not None and listed is not None
        legacy.value = "https://perso.example"
        db.delete(listed)
        migrated = db.get(Setting, "comet_urls_migrated")
        if migrated is not None:
            db.delete(migrated)
        db.commit()
        from mediatracker.providers.sources.comet_urls import migrate_comet_urls_setting

        migrate_comet_urls_setting(db, settings)
        db.commit()
        expected = default_comet_urls_from_legacy("https://perso.example")
        listed = db.get(Setting, "comet_base_urls")
        assert listed is not None
        assert parse_comet_url_setting_text(listed.value) == expected
        assert len(expected) == len(set(expected))
        migrate_comet_urls_setting(db, settings)
        db.commit()
        db.refresh(listed)
        assert parse_comet_url_setting_text(listed.value) == expected


def test_cleared_comet_urls_stays_empty_after_ensure_settings(client, app):
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "comet_base_urls")
        assert row is not None
        row.value = ""
        db.commit()
        ensure_settings(db, settings)
        ensure_settings(db, settings)
        db.refresh(row)
        assert (row.value or "").strip() == ""
        assert resolve_comet_base_urls(db, app.state.settings) == []


def test_explicit_empty_comet_urls_disables_resolve(client, app):
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "comet_base_urls")
        assert row is not None
        row.value = ""
        db.commit()
        assert resolve_comet_base_urls(db, app.state.settings) == []


def test_missing_comet_urls_row_falls_back_to_defaults(client, app):
    from sqlalchemy import select

    from mediatracker.db.models import Provider

    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "comet_base_urls")
        assert row is not None
        db.delete(row)
        provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "comet"))
        if provider is not None:
            provider.config = {}
        db.commit()
        assert resolve_comet_base_urls(db, app.state.settings) == list(DEFAULT_COMET_INSTANCE_URLS)


def test_parse_rejects_invalid_scheme():
    with pytest.raises(SettingsError):
        loads_comet_urls_from_api("ftp://invalide.example")


def test_migration_copies_legacy_comet_base_url(client, app):
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        ensure_settings(db, settings)
        legacy = db.get(Setting, "comet_base_url")
        assert legacy is not None
        legacy.value = "https://premiere.example"
        listed = db.get(Setting, "comet_base_urls")
        assert listed is not None
        db.delete(listed)
        migrated = db.get(Setting, "comet_urls_migrated")
        if migrated is not None:
            db.delete(migrated)
        db.commit()
        ensure_settings(db, settings)
        listed = db.get(Setting, "comet_base_urls")
        assert listed is not None
        assert parse_comet_url_setting_text(listed.value) == default_comet_urls_from_legacy(
            "https://premiere.example"
        )


def test_settings_patch_writes_ordered_list(client, app):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "comet_base_urls": "https://deux.example\nhttps://un.example\nhttps://deux.example",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    rows = {item["key"]: item["value"] for item in saved.json()["settings"]}
    assert rows["comet_base_urls"] == "https://deux.example\nhttps://un.example"
    assert rows["comet_base_url"] == "https://deux.example"
    with session_scope(app.state.session_factory) as db:
        assert resolve_comet_base_urls(db, app.state.settings) == ["https://deux.example", "https://un.example"]


def test_cascade_uses_second_instance_on_http_error():
    payload = json.dumps(_load("demo_hospital_s01e01.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "panne.example":
            return httpx.Response(503)
        return httpx.Response(200, text=payload)

    source = CometCascadeSource(
        ["https://panne.example", "https://ok.example"],
        delay=0,
        transport=httpx.MockTransport(handler),
    )
    try:
        links = source.links_for(QUERY, PREFS)
    finally:
        source.close()
    assert links
    assert links[0].external_id.startswith("comet:")


def test_cascade_uses_second_instance_when_first_returns_no_matching_streams():
    rich = json.dumps(_load("demo_hospital_s01e01.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "vide.example":
            return httpx.Response(200, text=json.dumps({"streams": []}))
        return httpx.Response(200, text=rich)

    source = CometCascadeSource(
        ["https://vide.example", "https://plein.example"],
        delay=0,
        transport=httpx.MockTransport(handler),
    )
    try:
        links = source.links_for(QUERY, PREFS)
    finally:
        source.close()
    assert links


def test_cascade_dedupes_info_hash_across_instances():
    payload = json.dumps(_load("demo_hospital_s01e01.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    source = CometCascadeSource(
        ["https://alpha.example", "https://beta.example"],
        delay=0,
        transport=httpx.MockTransport(handler),
    )
    try:
        links = source.links_for(QUERY, PREFS)
    finally:
        source.close()
    hashes = {item.external_id.split(":")[1] for item in links}
    assert len(hashes) == len(links)


def test_requests_never_carry_debrid_key():
    seen_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.strip("/")
        if path and path.split("/")[0] not in {"stream"}:
            seen_tokens.append(path.split("/")[0])
        return httpx.Response(200, json={"streams": []})

    source = CometSource(
        "https://comet-tokens.example",
        transport=httpx.MockTransport(handler),
        pacer=__import__("mediatracker.providers.sources.throttle", fromlist=["SourcePacer"]).SourcePacer(0),
    )
    source.quality_profile = PRINCIPAL
    try:
        source.links_for(QUERY, PREFS)
    finally:
        source.close()
    assert seen_tokens
    for token in seen_tokens:
        assert "debridApiKey" not in token
        padded = token + "=" * (-len(token) % 4)
        import base64

        raw = base64.urlsafe_b64decode(padded.encode())
        blob = json.loads(raw)
        assert blob.get("debridApiKey") in ("", None)


def test_format_roundtrip():
    text = format_comet_url_list(["https://a.example", "https://b.example"])
    assert parse_comet_url_setting_text(text) == ["https://a.example", "https://b.example"]


def test_normalize_comet_url_helper():
    assert normalize_comet_url(" https://x.example/ ") == "https://x.example"


def test_default_list_has_two_builtin_instances():
    assert list(DEFAULT_COMET_INSTANCE_URLS) == [
        "https://comet.feels.legal",
        "https://cometfortheweebs.midnightignite.me",
    ]
    assert "elfhosted" not in "\n".join(DEFAULT_COMET_INSTANCE_URLS)


def test_default_migration_keeps_custom_legacy_first():
    urls = default_comet_urls_from_legacy("https://perso.example")
    assert urls[0] == "https://perso.example"
    assert "https://comet.feels.legal" in urls


def test_default_migration_without_custom_legacy_uses_builtin_pair():
    assert default_comet_urls_from_legacy("https://comet.feels.legal") == list(DEFAULT_COMET_INSTANCE_URLS)


def test_cascade_skips_403_and_non_debrid_message():
    payload = json.dumps(_load("demo_hospital_s01e01.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bloque.example":
            return httpx.Response(403, text="Non-debrid searches disabled")
        return httpx.Response(200, text=payload)

    source = CometCascadeSource(
        ["https://bloque.example", "https://ok.example"],
        delay=0,
        transport=httpx.MockTransport(handler),
    )
    try:
        links = source.links_for(QUERY, PREFS)
    finally:
        source.close()
    assert links
