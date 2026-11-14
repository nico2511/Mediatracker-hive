"""Image de référence pour les mises à jour (MEDIATRACKER_UPDATE_IMAGE)."""

from __future__ import annotations

import httpx

from mediatracker.core import updates


def test_default_update_image_points_to_public_mtr(monkeypatch):
    monkeypatch.delenv("MEDIATRACKER_UPDATE_IMAGE", raising=False)
    assert updates.update_image() == updates.DEFAULT_UPDATE_IMAGE
    assert updates._parse_image_ref() == ("nico2511", "mediatracker-hive")
    assert updates._github_repo_for_image() == "nico2511/Mediatracker-hive"


def test_env_overrides_update_image_and_github_repo(monkeypatch):
    monkeypatch.setenv("MEDIATRACKER_UPDATE_IMAGE", "ghcr.io/nico2511/mediatracker:latest")
    assert updates.update_image() == "ghcr.io/nico2511/mediatracker:latest"
    assert updates._parse_image_ref() == ("nico2511", "mediatracker")
    assert updates._github_repo_for_image() == "nico2511/MediaTracker"


def test_anonymous_ghcr_pull_uses_registry_scope_without_basic_auth(client, app, monkeypatch):
    del client
    monkeypatch.delenv("MEDIATRACKER_UPDATE_IMAGE", raising=False)
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "aaaabbbbccccddddeeeeffff0000111122223333")
    seen_auth: list[str | None] = []
    seen_scope: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ghcr.io" and request.url.path == "/token":
            seen_auth.append(request.headers.get("authorization"))
            seen_scope.append(request.url.params.get("scope", ""))
            return httpx.Response(200, json={"token": "registry-anon"})
        if request.url.path.endswith("/v2/nico2511/mediatracker-hive/manifests/latest"):
            assert request.headers.get("authorization") == "Bearer registry-anon"
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

    transport = httpx.MockTransport(handler)
    db = app.state.session_factory()
    try:
        result = updates.check_for_update(db, app.state.settings, transport=transport)
    finally:
        db.close()
    assert result.available is True
    assert seen_auth == [None]
    assert seen_scope == ["repository:nico2511/mediatracker-hive:pull"]


def test_github_fallback_targets_mtr_repo(monkeypatch):
    monkeypatch.delenv("MEDIATRACKER_UPDATE_IMAGE", raising=False)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": "abc1234ffffffffff"})
        return httpx.Response(404)

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url, headers=kwargs.get("headers"))
        return handler(request)

    monkeypatch.setattr(updates.httpx, "get", fake_get)
    sha = updates._github_main("github_pat_test_value", None)
    assert sha.startswith("abc1234")
    assert any("/repos/nico2511/Mediatracker-hive/commits/main" in item for item in seen)
