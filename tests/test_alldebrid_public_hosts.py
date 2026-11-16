"""Liste publique des hébergeurs AllDebrid (repli statique)."""

from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, KNOWN_HOSTERS
from mediatracker.integrations import alldebrid_public_hosts as hosts_mod
from mediatracker.integrations.alldebrid_public_hosts import allowed_hoster_names, list_hoster_picker_options


def test_fallback_excludes_uptobox_and_keeps_1fichier_last():
    names = list_hoster_picker_options()
    assert names
    assert "uptobox" not in names
    assert "uptobox" not in allowed_hoster_names()
    assert names[-1] == "1fichier"
    assert not BLOCKED_SOURCE_HOSTERS.intersection(names)


def test_allowed_names_union_includes_known_hosters_missing_from_api():
    api_only = ["rapidgator", "turbobit"]
    merged = hosts_mod._merge_with_known_hosters(api_only)
    assert "nitroflare" in merged
    assert "tezfiles" in merged
    assert "uptobox" not in merged
    assert merged[-1] == "1fichier"


def test_parse_public_hosts_filters_non_hoster_keys():
    payload = {
        "status": "success",
        "data": {
            "hosts": {
                "rapidgator": [],
                "example": [],
                "alldebrid": [],
                "uptobox": [],
            }
        },
    }
    names = hosts_mod._parse_public_hosts(payload)
    assert "rapidgator" in names
    assert "example" not in names
    assert "alldebrid" not in names
    assert "uptobox" not in names


def test_hoster_preference_accepts_known_hosters_not_on_alldebrid_list(client, monkeypatch):
    from tests.conftest import csrf, login

    monkeypatch.setattr(hosts_mod, "_fetch_public_hosts", lambda: ["rapidgator", "turbobit", "uploady", "dailyuploads"])
    hosts_mod._cached_at = 0.0
    hosts_mod._cached_names = None
    login(client)
    response = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "nitroflare,rapidgator,tezfiles,1fichier"}},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    row = next(item for item in body["settings"] if item["key"] == "hoster_preference")
    tokens = row["value"].split(",")
    assert "nitroflare" in tokens
    assert "tezfiles" in tokens
    assert tokens[-1] == "1fichier"


def test_hoster_preference_rejects_unknown_name(client, monkeypatch):
    from tests.conftest import csrf, login

    monkeypatch.setattr(hosts_mod, "allowed_hoster_names", lambda *, force=False: frozenset(KNOWN_HOSTERS))
    login(client)
    response = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "rapidgator,not_a_hoster_xyz"}},
        headers=csrf(client),
    )
    assert response.status_code == 422
    assert "Hébergeur inconnu" in response.json()["detail"]