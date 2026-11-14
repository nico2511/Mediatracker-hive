"""Série : entrée hors saison (à classer) via lien manuel sans slot."""

from tests.conftest import csrf, login


def test_manual_link_without_slot_creates_ambiguous_null_season_bucket(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Parcours-exemple",
            "year": 2026,
            "new_episode_policy": "propose",
            "pref_quality": "HD",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]

    linked = client.post(
        "/api/v1/links",
        json={
            "media_id": media_id,
            "url": "https://example.invalid/release/neutral-pack",
            "title": "",
        },
        headers=csrf(client),
    )
    assert linked.status_code == 201, linked.text
    assert linked.json()["needs_slot"] is True

    items = client.get(f"/api/v1/media/{media_id}/items")
    assert items.status_code == 200, items.text
    body = items.json()
    bucket = next((row for row in body["seasons"] if row["number"] is None), None)
    assert bucket is not None
    assert bucket["episode_count"] == 1
    assert bucket["file_status_counts"] == {
        "present": 0,
        "trash": 0,
        "absent": 1,
        "downloading": 0,
    }
    episode = bucket["episodes"][0]
    assert episode["season"] is None
    assert episode["episode"] is None
    assert episode["state"] == "ambiguous"
    assert not episode["title"]
