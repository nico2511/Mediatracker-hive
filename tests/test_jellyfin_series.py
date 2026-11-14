"""Jellyfin user GUID resolution and series matching on a fiche."""

import logging

import httpx
from tests.conftest import csrf, login

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, from_api_item, resolve_user_id
from mediatracker.integrations.jellyfin_sync import diagnose_media

GUID = "6d7e8f90-a1b2-4c3d-8e9f-001122334455"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _episode(series_id: str, season: int, episode: int, *, played: bool) -> dict:
    return {
        "Type": "Episode",
        "Id": f"{series_id}-s{season}e{episode}",
        "SeriesId": series_id,
        "ParentIndexNumber": season,
        "IndexNumber": episode,
        "ProviderIds": {"Tmdb": "900000"},
        "UserData": {"Played": played},
    }


def _index() -> dict:
    return {
        "series-a": {
            "Name": "The Show",
            "OriginalTitle": "Le Show",
            "ProductionYear": 2020,
            "ProviderIds": {"Tmdb": "10", "Tvdb": "20", "Imdb": "tt10"},
        },
        "series-b": {
            "Name": "Show",
            "ProductionYear": 2019,
            "ProviderIds": {"Tmdb": "10"},
        },
    }


def test_duplicate_series_ids_with_the_same_tmdb_are_both_kept(client, app, caplog):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="The Show", year=2020, tmdb_id=10)
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=1, episode=1, state="missing"))
        db.add(MediaItem(media_id=media.id, season=2, episode=1, state="missing"))
        db.commit()
        index = _index()
        season_one = from_api_item(_episode("series-b", 1, 1, played=False), index)
        season_two = from_api_item(_episode("series-a", 2, 1, played=True), index)
        assert season_one is not None and season_two is not None
        with caplog.at_level(logging.INFO):
            report = diagnose_media(db, media, [season_one, season_two], None, index)
        assert report["series_ids"] == ["series-a", "series-b"]
        assert {row["episode"] for row in report["episodes"]} == {1}
        assert {row["season"] for row in report["episodes"]} == {1, 2}
        assert report["unmatched"] == []
        assert any("series-a" in line and "series-b" in line for line in caplog.messages)
        present = next(row for row in report["episodes"] if row["season"] == 1)
        assert present["played"] is False
        assert present["reason"] == "Présent dans Jellyfin, non marqué vu."
    finally:
        db.close()


def test_missing_series_is_one_summary_and_specials_are_quiet(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Absente", original_title="Missing", year=2018, tmdb_id=4)
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=0, episode=1, state="missing"))
        db.add(MediaItem(media_id=media.id, season=1, episode=1, state="missing"))
        db.add(MediaItem(media_id=media.id, season=1, episode=2, state="missing"))
        db.commit()
        other = from_api_item(
            _episode("other", 1, 1, played=False),
            {"other": {"Name": "Autre", "ProductionYear": 1999}},
        )
        assert other is not None
        report = diagnose_media(db, media, [other], None, {"other": {"Name": "Autre", "ProductionYear": 1999}})
        assert report["episodes"] == []
        assert report["unmatched"] == []
        assert report["summary"].startswith("Série introuvable dans Jellyfin.")
        assert "TMDB 4" in report["summary"]
        assert "titre original « Missing »" in report["summary"]
        assert "Série introuvable" not in "".join(row["reason"] for row in report["episodes"])
    finally:
        db.close()


def test_specials_are_shown_only_when_jellyfin_has_them(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="The Show", year=2020, tmdb_id=10)
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=0, episode=1, state="missing"))
        db.add(MediaItem(media_id=media.id, season=1, episode=1, state="missing"))
        db.commit()
        index = _index()
        regular = from_api_item(_episode("series-a", 1, 1, played=True), index)
        assert regular is not None
        hidden = diagnose_media(db, media, [regular], None, index)
        assert all(row["season"] != 0 for row in hidden["episodes"] + hidden["unmatched"])
        special = from_api_item(_episode("series-a", 0, 1, played=False), index)
        assert special is not None
        shown = diagnose_media(db, media, [regular, special], None, index)
        assert any(row["season"] == 0 and row["matched"] for row in shown["episodes"])
    finally:
        db.close()


def test_original_title_and_year_within_one_match(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Le Show", original_title="The Show", year=2021, tmdb_id=None)
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=1, episode=1, state="missing"))
        db.commit()
        index = {
            "series-a": {
                "Name": "Show, The",
                "OriginalTitle": "The Show",
                "ProductionYear": 2020,
                "ProviderIds": {},
            }
        }
        watch = from_api_item(_episode("series-a", 1, 1, played=False), index)
        assert watch is not None
        report = diagnose_media(db, media, [watch], None, index)
        assert report["series_ids"] == ["series-a"]
        assert report["episodes"][0]["matched"] is True
    finally:
        db.close()


def test_manual_link_is_used_before_the_title(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Tout autre", year=1990, tmdb_id=99, jellyfin_series_id="series-b")
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=1, episode=1, state="missing"))
        db.commit()
        index = _index()
        watch = from_api_item(_episode("series-b", 1, 1, played=True), index)
        assert watch is not None
        report = diagnose_media(db, media, [watch], None, index)
        assert "series-b" in report["series_ids"]
        assert report["episodes"][0]["played"] is True
        assert report["manual_series_id"] == "series-b"
    finally:
        db.close()


def test_unmatched_episodes_are_split_from_the_matched_ones(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="The Show", year=2020, tmdb_id=10)
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=2, episode=1, state="missing"))
        db.add(MediaItem(media_id=media.id, season=2, episode=3, state="missing"))
        db.commit()
        index = _index()
        present = from_api_item(_episode("series-a", 2, 1, played=True), index)
        assert present is not None
        report = diagnose_media(db, media, [present], None, index)
        assert len(report["episodes"]) == 1
        assert len(report["unmatched"]) == 1
        assert report["unmatched"][0]["episode"] == 3
        assert "absent" in report["unmatched"][0]["reason"]
    finally:
        db.close()


def test_username_resolves_to_the_guid_and_is_cached():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/Users":
            return httpx.Response(200, json=[{"Id": GUID, "Name": "Camille"}, {"Id": OTHER, "Name": "Alice"}])
        if request.url.path == f"/Users/{GUID}/Items":
            return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})
        return httpx.Response(400, json={"errors": {"userId": ["The value 'Camille' is not valid."]}})

    client = JellyfinClient("http://jellyfin.example", "key", "Camille", transport=httpx.MockTransport(handler))
    try:
        assert resolve_user_id(client, "camille") == GUID
        client.user_items(resolve_user_id(client, "Camille"))
    finally:
        client.close()
    assert calls.count("/Users") == 1
    assert f"/Users/{GUID}/Items" in calls
    assert not any("Camille" in path for path in calls)


def test_unknown_username_lists_the_accounts():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[{"Id": GUID, "Name": "Camille"}, {"Id": OTHER, "Name": "Alice"}])

    client = JellyfinClient("http://jellyfin.example", "key", None, transport=httpx.MockTransport(handler))
    try:
        try:
            resolve_user_id(client, "Bob")
        except JellyfinError as exc:
            code = exc.code
            message = exc.message
        else:
            raise AssertionError("expected JellyfinError")
    finally:
        client.close()
    assert code == "USER_NOT_FOUND"
    assert "Bob" in message
    assert "Alice" in message
    assert "Camille" in message


def test_settings_users_and_manual_link(client, monkeypatch):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.example",
                "jellyfin_api_key": "key",
                "jellyfin_user_id": "Camille",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text

    class _Server:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def users(self) -> list[dict]:
            return [{"Id": GUID, "Name": "Camille"}, {"Id": OTHER, "Name": "Alice"}]

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.api.v1.jellyfin.JellyfinClient", _Server)
    listed = client.post("/api/v1/integrations/jellyfin/users", json={}, headers=csrf(client))
    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()["users"]] == ["Camille", "Alice"]

    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "The Show", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]

    class _Search(_Server):
        def search_series(self, user_id: str, term: str) -> list[dict]:
            assert user_id == GUID
            assert term == "Show"
            return [{"Id": "series-b", "Name": "Show", "ProductionYear": 2019, "OriginalTitle": "The Show"}]

        def user_items(self, user_id: str) -> list[dict]:
            assert user_id == GUID
            return [_episode("series-b", 1, 1, played=False)]

        def series_index(self, user_id: str) -> dict:
            assert user_id == GUID
            return _index()

    monkeypatch.setattr("mediatracker.integrations.jellyfin.JellyfinClient", _Search)
    found = client.get(f"/api/v1/media/{media_id}/jellyfin/series", params={"q": "Show"})
    assert found.status_code == 200, found.text
    assert found.json()["items"][0]["id"] == "series-b"
    linked = client.put(
        f"/api/v1/media/{media_id}/jellyfin/link",
        json={"series_id": "series-b"},
        headers=csrf(client),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["series_id"] == "series-b"
    added = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "title": "Un"},
        headers=csrf(client),
    )
    assert added.status_code == 201, added.text
    diag = client.get(f"/api/v1/media/{media_id}/jellyfin")
    assert diag.status_code == 200, diag.text
    body = diag.json()
    assert body["manual_series_id"] == "series-b"
    assert "series-b" in body["series_ids"]
    assert body["episodes"][0]["played"] is False
    stored = client.get("/api/v1/settings").json()
    user = next(row for row in stored["settings"] if row["key"] == "jellyfin_user_id")
    assert user["value"] == GUID


def test_diag_route_caches_the_guid(client):
    """The fiche GET rewrites a username to the GUID it resolved."""
    login(client)
    client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.example",
                "jellyfin_api_key": "key",
                "jellyfin_user_id": "Camille",
            }
        },
        headers=csrf(client),
    )
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Vide", "year": 2024},
        headers=csrf(client),
    )

    class _Named:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def users(self) -> list[dict]:
            return [{"Id": GUID, "Name": "Camille"}]

        def user_items(self, user_id: str) -> list[dict]:
            assert user_id == GUID
            return []

        def series_index(self, user_id: str) -> dict:
            assert user_id == GUID
            return {}

        def close(self) -> None:
            return None

    # Imported inside the route from the integrations package.
    import mediatracker.integrations.jellyfin as jellyfin

    original = jellyfin.JellyfinClient
    jellyfin.JellyfinClient = _Named  # type: ignore[misc]
    try:
        diag = client.get(f"/api/v1/media/{created.json()['id']}/jellyfin")
    finally:
        jellyfin.JellyfinClient = original
    assert diag.status_code == 200, diag.text
    assert diag.json()["summary"].startswith("Série introuvable")
    assert diag.json()["episodes"] == []
    row = client.get("/api/v1/settings").json()
    user = next(item for item in row["settings"] if item["key"] == "jellyfin_user_id")
    assert user["value"] == GUID
