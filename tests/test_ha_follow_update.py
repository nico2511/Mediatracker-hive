"""Restart, update check, and follow-from-Home-Assistant."""

from datetime import date
from pathlib import Path

import httpx
from tests.conftest import csrf, login

from mediatracker.core.restart import container_stop
from mediatracker.db.models import Event, Media, MediaItem, Setting
from mediatracker.integrations.tmdb import TmdbTitle
from mediatracker.security.logging import redact


def _bearer(client, scopes: list[str], name: str) -> dict[str, str]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": name, "scopes": scopes},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


class _Tmdb:
    def __init__(self, token: str, **_kwargs) -> None:
        assert token == "tmdb-test-token"

    def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
        assert tmdb_id == 99
        assert media_type == "film"
        return TmdbTitle(
            tmdb_id=99,
            media_type="film",
            title="Demain",
            year=2026,
            overview=None,
            poster_url=None,
            series_status=None,
            release_date=date(2026, 1, 1),
            seasons=(),
        )

    def close(self) -> None:
        return None


def test_restart_pauses_and_schedules_a_nonzero_exit(client, monkeypatch):
    login(client)
    headers = _bearer(client, ["write"], "restart")
    read_headers = _bearer(client, ["read"], "restart-read")
    client.cookies.clear()
    seen: list[int] = []
    monkeypatch.setattr(
        "mediatracker.core.restart.schedule_container_stop",
        lambda code=1: seen.append(code),
    )
    denied = client.post("/api/v1/ha/actions/restart", headers=read_headers)
    assert denied.status_code == 403
    accepted = client.post("/api/v1/ha/actions/restart", headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert seen == [1]
    assert "pause" in accepted.json()["detail"].lower() or "Redémarrage" in accepted.json()["detail"]


def test_container_stop_writes_the_exit_code_for_s6(tmp_path: Path):
    halt = tmp_path / "halt"
    halt.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    halt.chmod(0o755)
    code_path = tmp_path / "results" / "exitcode"
    called: list[int] = []
    container_stop(1, halt=halt, exitcode_path=code_path, exit_fn=lambda code: called.append(code))
    assert code_path.read_text(encoding="ascii") == "1\n"
    assert called == []
    container_stop(1, halt=tmp_path / "missing", exit_fn=lambda code: called.append(code))
    assert called == [1]


def test_check_update_uses_the_image_label_and_hides_the_token(client, app, monkeypatch, caplog):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"ghcr_read_token": "ghp_test_secret_value"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    listed = {row["key"]: row for row in saved.json()["settings"]}
    assert listed["ghcr_read_token"]["value"] is None
    assert listed["ghcr_read_token"]["set"] is True
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aaaabbbbccccddddeeeeffff0000111122223333")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        if request.url.host == "ghcr.io" and request.url.path == "/token":
            assert "ghp_test_secret_value" not in str(request.url)
            return httpx.Response(200, json={"token": "registry-token"})
        if request.url.path.endswith("/manifests/latest"):
            assert request.headers["authorization"] == "Bearer registry-token"
            return httpx.Response(
                200,
                headers={"docker-content-digest": "sha256:index"},
                json={
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "annotations": {
                        "org.opencontainers.image.revision": "bbbbccccddddeeeeffff00001111222233334444"
                    },
                },
            )
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(
        "mediatracker.core.updates.httpx.Client",
        lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=5.0),
    )
    headers = _bearer(client, ["write"], "update")
    client.cookies.clear()
    with caplog.at_level("INFO"):
        checked = client.post("/api/v1/ha/actions/check-update", headers=headers)
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["update_available"] is True
    assert body["remote_sha"].startswith("bbbb")
    assert "ghp_test_secret_value" not in checked.text
    assert "ghp_test_secret_value" not in caplog.text
    assert all("ghp_test_secret_value" not in item for item in seen)
    summary = client.get("/api/v1/ha/summary", headers=headers)
    assert summary.json()["update_available"] is True
    db = app.state.session_factory()
    try:
        event = db.query(Event).filter(Event.type == "update.available").one()
        assert event.payload["remote_sha"].startswith("bbbb")
    finally:
        db.close()
    assert redact("token ghp_test_secret_value") == "token [redacted]"


def test_check_update_falls_back_to_main(client, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"github_update_token": "github_pat_test_value"}},
        headers=csrf(client),
    )
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "abc1234fff")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ghcr.io" and request.url.path == "/token":
            return httpx.Response(401)
        assert request.url.path.endswith("/commits/main")
        assert "/repos/nico2511/Mediatracker-hive/commits/main" in str(request.url)
        assert "github_pat_test_value" not in str(request.url)
        return httpx.Response(200, json={"sha": "abc1234ffffffffff"})

    real_client = httpx.Client
    monkeypatch.setattr(
        "mediatracker.core.updates.httpx.Client",
        lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=5.0),
    )
    monkeypatch.setattr(
        "mediatracker.core.updates.httpx.get",
        lambda *args, **kwargs: handler(httpx.Request("GET", args[0], headers=kwargs.get("headers"))),
    )
    headers = _bearer(client, ["write"], "github-update")
    client.cookies.clear()
    checked = client.post("/api/v1/ha/actions/check-update", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["update_available"] is False


def _ghcr_client(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        "mediatracker.core.updates.httpx.Client",
        lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler), timeout=5.0),
    )


def test_check_update_accepts_access_token_from_registry(client, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"ghcr_read_token": "ghp_test_secret_value"}},
        headers=csrf(client),
    )
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aaaabbbbccccddddeeeeffff0000111122223333")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ghcr.io" and request.url.path == "/token":
            return httpx.Response(200, content=b'{"access_token":"registry-token"}')
        if request.url.path.endswith("/manifests/latest"):
            return httpx.Response(
                200,
                headers={"docker-content-digest": "sha256:index"},
                json={
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "annotations": {
                        "org.opencontainers.image.revision": "bbbbccccddddeeeeffff00001111222233334444"
                    },
                },
            )
        return httpx.Response(404)

    _ghcr_client(monkeypatch, handler)
    headers = _bearer(client, ["write"], "update-access")
    client.cookies.clear()
    checked = client.post("/api/v1/ha/actions/check-update", headers=headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["update_available"] is True


def test_unreadable_registry_json_is_an_explicit_error(client, monkeypatch, caplog):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"ghcr_read_token": "ghp_test_secret_value"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aaaabbbbccccddddeeeeffff0000111122223333")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ghcr.io" and request.url.path == "/token":
            return httpx.Response(200, json={"token": "registry-token"})
        if request.url.path.endswith("/manifests/latest"):
            return httpx.Response(
                200,
                content=b"<!doctype html>",
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    _ghcr_client(monkeypatch, handler)
    headers = _bearer(client, ["write"], "update-json")
    client.cookies.clear()
    with caplog.at_level("WARNING"):
        checked = client.post("/api/v1/ha/actions/check-update", headers=headers)
    assert checked.status_code == 422, checked.text
    detail = checked.json()["detail"]
    assert "HTML" in detail
    assert "jeton GHCR" in detail
    assert "La comparaison a échoué" not in checked.text
    assert "ghp_test_secret_value" not in checked.text
    assert "ghp_test_secret_value" not in caplog.text


def test_unreadable_github_json_is_an_explicit_error(client, monkeypatch, caplog):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"github_update_token": "github_pat_test_value"}},
        headers=csrf(client),
    )
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "abc1234fff")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"})

    monkeypatch.setattr(
        "mediatracker.core.updates.httpx.get",
        lambda *args, **kwargs: handler(httpx.Request("GET", args[0], headers=kwargs.get("headers"))),
    )
    headers = _bearer(client, ["write"], "github-json")
    client.cookies.clear()
    with caplog.at_level("WARNING"):
        checked = client.post("/api/v1/ha/actions/check-update", headers=headers)
    assert checked.status_code == 422, checked.text
    assert checked.json()["detail"] == "La réponse de GitHub n'est pas un JSON lisible."
    assert "JSONDecodeError" in caplog.text
    assert "github_pat_test_value" not in checked.text
    assert "github_pat_test_value" not in caplog.text


def test_unclassified_update_error_names_the_exception(client, monkeypatch, caplog):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"ghcr_read_token": "ghp_test_secret_value"}},
        headers=csrf(client),
    )
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aaaabbbbccccddddeeeeffff0000111122223333")

    def boom(*_args, **_kwargs):
        raise RuntimeError("échec ghp_test_secret_value")

    monkeypatch.setattr("mediatracker.core.updates._ghcr_revision", boom)
    headers = _bearer(client, ["write"], "update-boom")
    client.cookies.clear()
    with caplog.at_level("WARNING"):
        checked = client.post("/api/v1/ha/actions/check-update", headers=headers)
    assert checked.status_code == 422, checked.text
    assert checked.json()["detail"] == "Comparaison impossible (RuntimeError)."
    assert "RuntimeError" in caplog.text
    assert "[redacted]" in caplog.text
    assert "ghp_test_secret_value" not in checked.text
    assert "ghp_test_secret_value" not in caplog.text


def test_follow_from_home_assistant_is_idempotent_and_attaches_files(client, app, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"tmdb_read_token": "tmdb-test-token"}},
        headers=csrf(client),
    )
    monkeypatch.setattr("mediatracker.integrations.tmdb.TmdbClient", _Tmdb)
    films = Path(app.state.settings.data_dir) / "media" / "Films" / "Demain (2026) [tmdbid-99]"
    films.mkdir(parents=True)
    (films / "Demain (2026) [tmdbid-99].mkv").write_bytes(b"video")
    headers = _bearer(client, ["write"], "follow")
    read_headers = _bearer(client, ["read"], "follow-read")
    client.cookies.clear()
    denied = client.post(
        "/api/v1/ha/actions/follow",
        headers=read_headers,
        json={"tmdb_id": 99, "type": "movie"},
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/v1/ha/actions/follow",
        headers=headers,
        json={"tmdb_id": 99, "type": "movie", "auto_download": False},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["already"] is False
    assert body["title"] == "Demain"
    assert body["attached"] == 0
    from mediatracker.core.attach_deferred import join_pending

    join_pending()
    again = client.post(
        "/api/v1/ha/actions/follow",
        headers=headers,
        json={"tmdb_id": 99, "type": "movie"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["already"] is True
    assert again.json()["title"] == "Demain"
    assert "déjà suivi" in again.json()["detail"]
    db = app.state.session_factory()
    try:
        media = db.query(Media).filter(Media.tmdb_id == 99).one()
        assert media.followed is True
        assert media.auto_download is False
        item = db.query(MediaItem).filter(MediaItem.media_id == media.id).one()
        assert item.downloaded is True
        assert db.query(Setting).filter(Setting.key == "tmdb_read_token").one()
    finally:
        db.close()
