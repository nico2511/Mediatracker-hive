from tests.conftest import csrf, login


def test_login_sets_session_without_secure_flag(client):
    response = login(client)
    cookies = response.headers.get_list("set-cookie")
    session = next(item for item in cookies if item.startswith("mt_session="))
    csrf_cookie = next(item for item in cookies if item.startswith("mt_csrf="))
    assert "httponly" in session.lower()
    assert "httponly" not in csrf_cookie.lower()
    assert "secure" not in session.lower().split(";", 1)[-1]
    assert "samesite=lax" in session.lower()
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_bad_password_and_rate_limit(client, app):
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")
    blocked = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert blocked.status_code == 429
    # Argon2 takes a moment, so the remaining lock is at most 15 minutes.
    assert 840 <= int(blocked.headers["retry-after"]) <= 900
    app.state.login_limiter.reset()
    assert login(client).status_code == 200


def test_logout_requires_csrf_and_clears_session(client):
    login(client)
    rejected = client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200
    gone = client.post("/api/v1/auth/logout", headers=csrf(client))
    assert gone.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_healthz_is_public_and_docs_require_login(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert client.get("/api/docs", follow_redirects=False).status_code == 302
    assert client.get("/api/openapi.json").status_code == 401
    login(client)
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_password_is_argon2(client, app):
    from sqlalchemy import select

    from mediatracker.db.models import User

    db = app.state.session_factory()
    try:
        user = db.scalar(select(User).where(User.username == "admin"))
        assert user.password_hash.startswith("$argon2")
    finally:
        db.close()
