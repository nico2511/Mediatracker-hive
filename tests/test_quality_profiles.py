"""Quality profiles are stored, edited, and applied to Comet and Wawacity alike."""

import base64
import json
from pathlib import Path

import httpx
from tests.conftest import csrf, login

from mediatracker.core.quality_profile import Profile, comet_config_token, default_profile, detect_language
from mediatracker.core.source_select import select_variants
from mediatracker.providers.base import EpisodeQuery, SourcePreferences, SourceResult
from mediatracker.providers.sources.comet import CometSource, select_streams
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer

FIXTURES = Path(__file__).parent / "fixtures" / "comet"
BUILDING_DEMO = EpisodeQuery(
    title="Immeuble Démo",
    year=2021,
    media_type="series",
    imdb_id="tt9000001",
    season=3,
    episode=1,
)
PREFS = SourcePreferences()


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _decode(token: str) -> dict:
    pad = "=" * (-len(token) % 4)
    return json.loads(base64.urlsafe_b64decode(token + pad))


def test_default_profile_is_seeded_and_can_be_edited(client):
    login(client)
    listed = client.get("/api/v1/quality-profiles")
    assert listed.status_code == 200, listed.text
    profiles = listed.json()["profiles"]
    assert profiles[0]["name"] == "VF/Multi 1080p"
    assert profiles[0]["is_default"] is True
    assert profiles[0]["languages"] == ["VFF", "VFQ", "VF", "MULTI", "VOSTFR"]
    assert profiles[0]["max_resolution"] == "1080p"
    assert profiles[0]["min_resolution"] == "1080p"
    by_name = {row["name"]: row for row in profiles}
    assert set(by_name) >= {
        "VF/Multi 1080p",
        "4K Multi",
        "720p VF",
        "VOSTFR 1080p",
        "Meilleure dispo",
        "HDLight VF/Multi",
    }
    assert sum(1 for row in profiles if row["is_default"]) == 1
    assert by_name["4K Multi"]["min_resolution"] == "4K"
    assert by_name["4K Multi"]["max_resolution"] == "4K"
    assert by_name["4K Multi"]["languages"] == ["MULTI", "VFF", "VF"]
    assert by_name["720p VF"]["min_resolution"] == "HD"
    assert by_name["720p VF"]["max_resolution"] == "HD"
    assert by_name["VOSTFR 1080p"]["languages"] == ["VOSTFR"]
    assert by_name["VOSTFR 1080p"]["max_resolution"] == "1080p"
    assert by_name["Meilleure dispo"]["min_resolution"] is None
    assert by_name["Meilleure dispo"]["max_resolution"] is None
    assert by_name["Meilleure dispo"]["languages"] == ["VFF", "VFQ", "VF", "MULTI", "VOSTFR", "VO"]

    created = client.post(
        "/api/v1/quality-profiles",
        json={
            "name": "VOSTFR compact",
            "languages": ["VOSTFR", "TRUEFRENCH"],
            "max_resolution": "HD",
            "max_size_mb": 1500,
            "exclude_keywords": ["HDR", " CAM "],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["languages"] == ["VOSTFR", "VFF"]
    assert body["exclude_keywords"] == ["HDR", "CAM"]
    assert body["is_default"] is False

    default_id = profiles[0]["id"]
    refused = client.delete(f"/api/v1/quality-profiles/{default_id}", headers=csrf(client))
    assert refused.status_code == 422
    assert "par défaut" in refused.json()["detail"]

    promoted = client.patch(
        f"/api/v1/quality-profiles/{body['id']}",
        json={"is_default": True},
        headers=csrf(client),
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["is_default"] is True

    removed = client.delete(f"/api/v1/quality-profiles/{default_id}", headers=csrf(client))
    assert removed.status_code == 204, removed.text

    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Archipel Pilote", "year": 2021},
        headers=csrf(client),
    )
    assert media.status_code == 201, media.text
    assert media.json()["quality_profile_id"] is None
    pinned = client.patch(
        f"/api/v1/media/{media.json()['id']}",
        json={"quality_profile_id": body["id"]},
        headers=csrf(client),
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["quality_profile_id"] == body["id"]
    cleared = client.patch(
        f"/api/v1/media/{media.json()['id']}",
        json={"quality_profile_id": None},
        headers=csrf(client),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["quality_profile_id"] is None


def test_real_comet_response_keeps_french_and_drops_2160p_english():
    chosen = select_streams(_load("demo_building_s03e01.json"), BUILDING_DEMO, PREFS, profile=default_profile())
    assert chosen
    assert all(item.quality != "4K" for item in chosen)
    assert all(item.language in {"VFF", "VFQ", "VF", "MULTI", "VOSTFR"} for item in chosen)
    assert "f57a263b4741e796fb0761a84bf15506c4ac01a2" not in {item.external_id for item in chosen}
    order = default_profile().languages
    indexes = [order.index(item.language or "") for item in chosen]
    assert indexes == sorted(indexes)
    assert chosen[0].language in {"VF", "MULTI"}
    assert chosen[0].quality == "1080p"
    assert any(item.language == "MULTI" and item.quality == "1080p" for item in chosen)
    assert any(item.language in {"VF", "VFF", "VFQ", "MULTI"} for item in chosen)


def test_french_flag_counts_and_keywords_and_size_apply():
    assert detect_language("Show.S01E01.1080p.WEB.mkv\n🇫🇷") == "VF"
    assert detect_language("Show.S01E01.VOSTFR.mkv\n🇫🇷") == "VOSTFR"
    assert detect_language("Show.S01E01.2160p.mkv\n🇬🇧") == "EN"
    profile = Profile(
        name="Sans HDR",
        languages=("VF", "MULTI"),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=1000,
        exclude_keywords=("HDR",),
    )
    payload = {
        "streams": [
            _stream("a" * 40, "Immeuble Démo S03E01 1080p HDR MULTI.mkv", "🇫🇷", 900 * 1024 * 1024),
            _stream("b" * 40, "Immeuble Démo S03E01 1080p MULTI.mkv", "🇫🇷", 3 * 1024 * 1024 * 1024),
            _stream(
                "c" * 40,
                "Immeuble Démo S03E01 1080p.mkv",
                "🇫🇷",
                800 * 1024 * 1024,
                name="Comet 1080p",
            ),
        ]
    }
    chosen = select_streams(payload, BUILDING_DEMO, PREFS, profile=profile)
    assert [item.external_id for item in chosen] == [f"comet:{'c' * 40}:0"]
    assert chosen[0].language == "VF"


def test_nothing_matching_explains_the_profile_and_the_config_hides_the_key():
    token = comet_config_token(default_profile())
    decoded = _decode(token)
    assert decoded["debridService"] == "torrent"
    assert decoded["debridApiKey"] == ""
    assert "alldebrid" not in json.dumps(decoded).lower()
    assert decoded["resolutions"]["2160p"] is False
    assert decoded["resolutions"]["1080p"] is True
    payload = json.dumps(
        {
            "streams": [
                _stream(
                    "d" * 40,
                    "Immeuble Démo S03E01 2160p English.mkv",
                    "🇬🇧",
                    4 * 1024 * 1024 * 1024,
                    name="Comet 2160p",
                )
            ]
        }
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert "alldebrid" not in request.url.path.lower()
        return httpx.Response(200, text=payload)

    source = CometSource(
        "https://comet.feels.legal",
        transport=httpx.MockTransport(handler),
        cache=HtmlCache(0),
        pacer=SourcePacer(0),
    )
    source.quality_profile = default_profile()
    try:
        assert source.links_for(BUILDING_DEMO, PREFS) == []
    finally:
        source.close()
    assert token in seen[0]
    assert source.last_empty_reason is not None
    assert "VF/Multi 1080p" in source.last_empty_reason


def test_wawacity_pages_follow_the_same_language_order():
    results = [
        SourceResult(
            title="Agent Nuit Démo",
            year=2023,
            media_type="series",
            ref="hd",
            url="http://hd",
            season=1,
            quality="HD",
            language="VFF",
        ),
        SourceResult(
            title="Agent Nuit Démo",
            year=2023,
            media_type="series",
            ref="multi",
            url="http://multi",
            season=1,
            quality="1080p",
            language="MULTI",
        ),
        SourceResult(
            title="Agent Nuit Démo",
            year=2023,
            media_type="series",
            ref="ultra",
            url="http://ultra",
            season=1,
            quality="4K",
            language="MULTI",
        ),
        SourceResult(
            title="Agent Nuit Démo",
            year=2023,
            media_type="series",
            ref="vost",
            url="http://vost",
            season=1,
            quality="1080p",
            language="VOSTFR",
        ),
    ]
    chosen = select_variants(
        results,
        title="Agent Nuit Démo",
        year=2023,
        media_type="series",
        seasons={1},
        quality="HD",
        language="VFF",
        allow_fallback=True,
        quality_fallback=["HD", "1080p"],
        language_fallback=["VFF", "MULTI"],
        profile=default_profile(),
    )
    assert [item.ref for item in chosen] == ["multi"]


def _stream(info: str, filename: str, flag: str, size: int, *, name: str = "Comet") -> dict:
    return {
        "name": name,
        "description": f"📄 {filename}\n👤 4 💾 1 GB\n{flag}",
        "infoHash": info,
        "fileIdx": 0,
        "behaviorHints": {"filename": filename, "videoSize": size},
    }
