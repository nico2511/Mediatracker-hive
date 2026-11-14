"""F9 — quotas et statuts hôtes AllDebrid (cache /user/hosts) avant unlock."""

import httpx
from tests.conftest import csrf, link_create_json, login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import tick
from mediatracker.core.selection import parse_hoster_preference
from mediatracker.db.models import Link
from mediatracker.providers.base import LinkCandidate
from mediatracker.providers.debrid.alldebrid import (
    AllDebridClient,
    AllDebridConfig,
    AllDebridProvider,
    reorder_hoster_preference_for_cycle,
)

NOW = __import__("datetime").datetime(2026, 10, 22, 12, tzinfo=__import__("datetime").timezone.utc)


def _client(handler, **kwargs) -> AllDebridClient:
    return AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None, **kwargs)


def _hosts_payload(hosts: dict) -> dict:
    return {"status": "success", "data": {"hosts": hosts}}


def test_supports_rejects_zero_quota_without_extra_network_call():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json=_hosts_payload(
                {
                    "rapidgator.net": {"status": True, "quota": 0, "limitSimuDl": 5},
                    "turbobit.net": {"status": True, "quota": 10, "limitSimuDl": 2},
                }
            ),
        )

    provider = AllDebridProvider(AllDebridConfig(api_key="test-key"))
    provider.client = _client(handler)
    rapid = LinkCandidate(url="https://rg", kind="ddl_hoster", hoster="rapidgator", external_id="a")
    turbo = LinkCandidate(url="https://tb", kind="ddl_hoster", hoster="turbobit", external_id="b")
    assert provider.supports(rapid) is False
    reason = provider.pop_skip_reason()
    assert reason is not None
    assert provider.supports(turbo) is True
    assert reason[0] == "HOST_QUOTA"
    assert "rapidgator" in reason[1]
    assert calls["n"] == 1


def test_supports_rejects_down_hoster():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hosts_payload({"turbobit.net": {"status": False}}),
        )

    provider = AllDebridProvider(AllDebridConfig(api_key="test-key"))
    provider.client = _client(handler)
    link = LinkCandidate(url="https://tb", kind="ddl_hoster", hoster="turbobit", external_id="a")
    assert provider.supports(link) is False
    reason = provider.pop_skip_reason()
    assert reason is not None
    assert reason[0] == "LINK_HOST_UNAVAILABLE"


def test_supports_rejects_zero_limit_simu_dl():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hosts_payload({"rapidgator.net": {"status": True, "quota": 5, "limitSimuDl": 0}}),
        )

    provider = AllDebridProvider(AllDebridConfig(api_key="test-key"))
    provider.client = _client(handler)
    link = LinkCandidate(url="https://rg", kind="ddl_hoster", hoster="rapidgator", external_id="a")
    assert provider.supports(link) is False


def test_get_hosts_uses_cache_for_ten_minutes():
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_hosts_payload({"rapidgator.net": {"status": True}}))

    client = _client(handler, hosts_ttl=600.0)
    client.get_hosts()
    client.get_hosts()
    assert calls["n"] == 1
    client.get_hosts(force=True)
    assert calls["n"] == 2


def test_reorder_moves_down_and_quota_hosters_to_the_end():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_hosts_payload(
                {
                    "rapidgator.net": {"status": False},
                    "turbobit.net": {"status": True, "quota": 0},
                    "uploady.io": {"status": True, "quota": 3},
                    "dailyuploads.net": {"status": True},
                    "1fichier.com": {"status": True},
                }
            ),
        )

    client = _client(handler)
    base = parse_hoster_preference("rapidgator,turbobit,uploady,dailyuploads,1fichier")
    ordered = reorder_hoster_preference_for_cycle(base, client)
    assert ordered == ["uploady", "dailyuploads", "rapidgator", "turbobit", "1fichier"]
    assert ordered[-1] == "1fichier"


def test_reorder_unchanged_when_hosts_api_errors():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "error": {"code": "AUTH", "message": "nope"}})

    client = _client(handler)
    base = parse_hoster_preference("rapidgator,turbobit,1fichier")
    assert reorder_hoster_preference_for_cycle(base, client) == base


def test_uptobox_stays_blocked_in_settings(client):
    login(client)
    bad = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "rapidgator,uptobox,1fichier"}},
        headers=csrf(client),
    )
    assert bad.status_code in {400, 422}


def test_pipeline_logs_hoster_quota_in_journal(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    first = _link(client, media["id"], "https://rapidgator.net/file/quota-a", 4)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://turbobit.net/file/quota-b",
            season=1,
            episode=4,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text

    class QuotaDebrid(FakeDebrid):
        def preference_for_cycle(self, preference):
            return reorder_hoster_preference_for_cycle(
                preference,
                _client(
                    lambda _r: httpx.Response(
                        200,
                        json=_hosts_payload({"rapidgator.net": {"status": True, "quota": 0}}),
                    )
                ),
            )

    debrid = QuotaDebrid()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=FakeDownloader())
        rapid = db.get(Link, first["id"])
        assert rapid.dead is False
        assert debrid.seen and debrid.seen[0].startswith("https://turbobit.net/")
    finally:
        db.close()
