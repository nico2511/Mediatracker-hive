"""ensure_admin : politique de mot de passe alignée sur password_policy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from mediatracker.bootstrap import ensure_admin
from mediatracker.config import Settings
from mediatracker.db.migrate import run_migrations
from mediatracker.db.models import User
from mediatracker.db.session import create_session_factory
from mediatracker.security.passwords import hash_password, verify_password


def _settings(tmp_path, **overrides):
    base = {
        "secret_key": "test-secret-key-0123456789",
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "data_dir": tmp_path / "data",
        "config_dir": tmp_path / "config",
        "disk_min_free_gb": 0,
        "admin_username": "admin",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def bootstrap_session(tmp_path):
    settings = _settings(tmp_path)
    run_migrations(settings.database_url)
    engine, factory = create_session_factory(settings)
    db = factory()
    try:
        yield db, settings
    finally:
        db.close()
        engine.dispose()


def test_ensure_admin_rejects_short_admin_password(bootstrap_session, caplog):
    db, settings = bootstrap_session
    settings = settings.model_copy(update={"admin_password": "court1!"})
    with caplog.at_level("ERROR"):
        ensure_admin(db, settings)
    assert db.scalar(select(User).where(User.username == settings.admin_username)) is None
    assert "ADMIN_PASSWORD refusé" in caplog.text
    assert "court1!" not in caplog.text


def test_ensure_admin_creates_account_when_password_policy_ok(bootstrap_session):
    db, settings = bootstrap_session
    password = "Choix-Solide-42!"
    settings = settings.model_copy(update={"admin_password": password})
    ensure_admin(db, settings)
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    assert user is not None
    assert verify_password(user.password_hash, password)


def test_ensure_admin_leaves_existing_account_untouched(bootstrap_session, caplog):
    db, settings = bootstrap_session
    original_hash = hash_password("Mot-Initial-99!")
    db.add(
        User(
            username=settings.admin_username,
            password_hash=original_hash,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    settings = settings.model_copy(update={"admin_password": "court1!"})
    with caplog.at_level("ERROR"):
        ensure_admin(db, settings)
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    assert user is not None
    assert user.password_hash == original_hash
    assert "ADMIN_PASSWORD refusé" not in caplog.text