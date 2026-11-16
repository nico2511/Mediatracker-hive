from tests.conftest import csrf, login

from mediatracker.providers.debrid.alldebrid import AllDebridError, AllDebridProvider
from mediatracker.providers.sources.manual import classify_url, hoster_of


def test_manual_classification():
    assert classify_url("https://dl-protect.link/abc?fn=1") == "ddl_protected"
    assert classify_url("https://rapidgator.net/file/abc") == "ddl_hoster"
    assert hoster_of("https://turbobit.net/abc.html") == "turbobit"
    assert classify_url("magnet:?xt=urn:btih:abc") == "magnet"
    assert classify_url("https://cdn.example/video.mkv") == "direct"


def test_builtin_providers_and_masked_key(client):
    login(client)
    listed = client.get("/api/v1/providers").json()
    types = {item["type"] for item in listed["items"]}
    assert types == {
        "manual",
        "wawacity",
        "free_telecharger",
        "comet",
        "torrentio",
        "stremio_addons",
        "alldebrid",
        "aria2",
        "hive",
    }
    manual = next(item for item in listed["items"] if item["type"] == "manual")
    health = client.get(f"/api/v1/providers/{manual['id']}/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    removed = client.delete(f"/api/v1/providers/{manual['id']}", headers=csrf(client))
    assert removed.status_code == 409
    known = client.get("/api/v1/providers/types").json()
    assert "aria2" in {item["type"] for item in known["items"]}


def test_alldebrid_auth_blocked_is_visible(client, monkeypatch):
    class FakeClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def get_user(self):
            raise AllDebridError("AUTH_BLOCKED", "New location")

    monkeypatch.setattr(AllDebridProvider, "client_factory", FakeClient)
    login(client)
    providers = client.get("/api/v1/providers").json()["items"]
    row = next(item for item in providers if item["type"] == "alldebrid")
    patched = client.patch(
        f"/api/v1/providers/{row['id']}",
        json={"enabled": True, "config": {"api_key": "test-key"}},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["config"]["api_key"] is None
    assert "api_key" in patched.json()["secrets_set"]
    health = client.get(f"/api/v1/providers/{row['id']}/health")
    body = health.json()
    assert body["status"] == "down"
    assert body["code"] == "AUTH_BLOCKED"
    assert "e-mail" in body["message"]
