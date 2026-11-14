"""En-têtes de durcissement HTTP (audit sécurité #14)."""

import pytest

from mediatracker.api.app import _spa_dir
from mediatracker.security.headers import _CONTENT_SECURITY_POLICY

_EXPECTED = {
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "content-security-policy": _CONTENT_SECURITY_POLICY,
}


def _assert_security_headers(response, *, expect_csp: bool = True) -> None:
    for name, value in _EXPECTED.items():
        if name == "content-security-policy" and not expect_csp:
            assert response.headers.get(name) is None
            continue
        assert response.headers.get(name) == value


def test_security_headers_on_api_route(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    _assert_security_headers(response)


@pytest.mark.skipif(_spa_dir() is None, reason="frontend dist absent")
def test_security_headers_on_spa_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    _assert_security_headers(response)


def test_security_headers_on_csrf_rejection(client):
    from tests.conftest import login

    login(client)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    _assert_security_headers(response)


@pytest.mark.skipif(_spa_dir() is None, reason="frontend dist absent")
def test_spa_build_assets_served_with_headers(client):
    dist = _spa_dir()
    assert dist is not None
    index = (dist / "index.html").read_text(encoding="utf-8")
    for line in index.splitlines():
        if 'src="/assets/' in line:
            path = line.split('src="', 1)[1].split('"', 1)[0]
            break
        if 'href="/assets/' in line and ".css" in line:
            path = line.split('href="', 1)[1].split('"', 1)[0]
    else:
        pytest.fail("no bundled asset reference in index.html")
    asset = client.get(path)
    assert asset.status_code == 200
    _assert_security_headers(asset)


def test_csp_img_src_allows_external_https_posters():
    """tmdbPosterSrc renvoie l'URL brute hors image.tmdb.org (catalogue, Jellyfin, etc.)."""
    assert "img-src 'self' data: https:" in _CONTENT_SECURITY_POLICY
    jellyfin_poster = "https://jellyfin.lan/Items/abc/Images/Primary?tag=1"
    source_poster = "https://wawacity.example/cover.jpg"
    for url in (jellyfin_poster, source_poster):
        assert url.startswith("https:")


def test_openapi_docs_skip_csp_but_keep_other_headers(client):
    from tests.conftest import login

    login(client)
    for path in ("/api/docs", "/api/redoc"):
        response = client.get(path)
        assert response.status_code == 200
        assert "cdn.jsdelivr.net" in response.text
        _assert_security_headers(response, expect_csp=False)
