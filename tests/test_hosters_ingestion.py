"""Hoster registry and source ingestion allow-list."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from mediatracker.core.hosters import hoster_is_blocked, source_ingestion_hosters
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, parse_hoster_preference
from mediatracker.providers.base import SourceRef
from mediatracker.providers.sources.free_telecharger import FreeTelechargerConfig, FreeTelechargerSource
from mediatracker.providers.sources.free_telecharger_html import PageLink as FtPageLink
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.providers.sources.wawacity_html import PageLink as WawaPageLink


def test_parse_hoster_preference_rejects_blocked_via_hoster_is_blocked():
    for name in BLOCKED_SOURCE_HOSTERS:
        assert hoster_is_blocked(name)
        with pytest.raises(ValueError, match="exclu"):
            parse_hoster_preference(f"rapidgator,{name}")


def test_settings_patch_rejects_blocked_hoster_preference(client):
    from tests.conftest import csrf, login

    login(client)
    bad = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "rapidgator,uptobox,1fichier"}},
        headers=csrf(client),
    )
    assert bad.status_code in {400, 422}


def test_source_ingestion_hosters_includes_custom_preference_minus_blocked():
    custom = "fictivebroker"
    merged = source_ingestion_hosters([custom, "uptobox", "rapidgator"])
    assert custom in merged
    assert "rapidgator" in merged
    assert not BLOCKED_SOURCE_HOSTERS.intersection(merged)


def test_wawacity_get_links_keeps_fictive_preference_hoster():
    base = "https://catalog.example.invalid"
    source = WawacitySource(
        WawacityConfig(base_url=base),
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
    )
    link = WawaPageLink(
        url="https://dl-protect.example.invalid/x",
        hoster="fictivebroker",
        season=1,
        episode=1,
        quality="HD",
        language="VFF",
    )
    page_mock = MagicMock(return_value=(MagicMock(title="T", media_type="series"), [link], []))
    with patch.object(source, "_page", page_mock):
        without = [item.hoster for item in source.get_links(SourceRef(ref=f"{base}/p", url=f"{base}/p"))]
        with_pref = [
            item.hoster
            for item in source.get_links(
                SourceRef(ref=f"{base}/p", url=f"{base}/p"),
                hoster_preference=["fictivebroker"],
            )
        ]
    source.close()
    assert "fictivebroker" not in without
    assert "fictivebroker" in with_pref


def test_wawacity_get_links_still_drops_blocked_hoster():
    base = "https://catalog.example.invalid"
    source = WawacitySource(
        WawacityConfig(base_url=base),
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
    )
    link = WawaPageLink(
        url="https://dl-protect.example.invalid/u",
        hoster="uptobox",
        season=1,
        episode=1,
        quality="HD",
        language="VFF",
    )
    page_mock = MagicMock(return_value=(MagicMock(title="T", media_type="series"), [link], []))
    with patch.object(source, "_page", page_mock):
        links = source.get_links(
            SourceRef(ref=f"{base}/p", url=f"{base}/p"),
            hoster_preference=["uptobox", "rapidgator"],
        )
    source.close()
    assert links == []


def test_free_telecharger_get_links_respects_preference_allow_list():
    base = "https://ft.example.invalid"
    source = FreeTelechargerSource(
        FreeTelechargerConfig(base_url=base),
        transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
    )
    link = FtPageLink(
        url="https://dl-protect.example.invalid/f",
        hoster="fictivebroker",
        season=None,
        episode=None,
        quality="HD",
        language="VFF",
    )
    page_mock = MagicMock(return_value=(MagicMock(quality="HD", language="VFF"), [link]))
    with patch.object(source, "_page", page_mock):
        kept = source.get_links(
            SourceRef(ref=f"{base}/f", url=f"{base}/f"),
            hoster_preference=["fictivebroker"],
        )
    source.close()
    assert len(kept) == 1
    assert kept[0].hoster == "fictivebroker"