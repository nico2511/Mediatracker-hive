"""Docker build id fallback when GIT_SHA is not a real commit."""

from pathlib import Path

from tests.conftest import login
from tests.test_installed_version_notify import _seed_fingerprint, _set_notify_on_update

from mediatracker.core import build_info
from mediatracker.core.build_info import fingerprint_suffix, short_build_id
from mediatracker.core.installed_version import installed_fingerprint, observe_installed_version
from mediatracker.db.models import Event
from mediatracker.db.session import session_scope
from mediatracker.events.text import notification_text


def _use_build_id_file(monkeypatch, tmp_path: Path, value: str | None) -> None:
    build_info.build_id.cache_clear()
    if value is None:
        monkeypatch.setattr(build_info, "_build_id_paths", lambda: (tmp_path / "absent",))
    else:
        path = tmp_path / ".build-id"
        path.write_text(value, encoding="utf-8")
        monkeypatch.setattr(build_info, "_build_id_paths", lambda: (path,))
    build_info.build_id.cache_clear()


def test_short_build_id_uses_eight_hex_chars(monkeypatch, tmp_path):
    _use_build_id_file(monkeypatch, tmp_path, "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    assert short_build_id() == "a1b2c3d4"


def test_missing_build_id_file_is_safe(monkeypatch, tmp_path):
    _use_build_id_file(monkeypatch, tmp_path, None)
    assert build_info.build_id() is None
    monkeypatch.delenv("MEDIATRACKER_GIT_SHA", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert fingerprint_suffix() == ""
    assert installed_fingerprint().endswith("@")


def test_build_id_change_without_git_sha_emits_event(client, app, monkeypatch, tmp_path):
    login(client)
    monkeypatch.delenv("MEDIATRACKER_GIT_SHA", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    _use_build_id_file(monkeypatch, tmp_path, "11111111-1111-4111-8111-111111111111")
    with session_scope(app.state.session_factory) as db:
        _seed_fingerprint(db, f"2.0.0@{short_build_id()}")
        _set_notify_on_update(db, True)
    _use_build_id_file(monkeypatch, tmp_path, "22222222-2222-4222-8222-222222222222")
    with session_scope(app.state.session_factory) as db:
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        event = db.query(Event).filter_by(type="system.updated").one()
        assert event.payload["new_sha"] is None
        assert event.payload["new_build_id"] == "22222222"


def test_same_build_id_restart_is_silent(client, app, monkeypatch, tmp_path):
    login(client)
    monkeypatch.delenv("MEDIATRACKER_GIT_SHA", raising=False)
    _use_build_id_file(monkeypatch, tmp_path, "33333333-3333-4333-8333-333333333333")
    suffix = short_build_id()
    with session_scope(app.state.session_factory) as db:
        _seed_fingerprint(db, f"2.0.0@{suffix}")
    with session_scope(app.state.session_factory) as db:
        observe_installed_version(db, app.state.settings)
    with session_scope(app.state.session_factory) as db:
        assert db.query(Event).filter_by(type="system.updated").count() == 0


def test_system_updated_notification_prefers_sha_or_build_date():
    title, message = notification_text(
        "system.updated",
        {
            "new_version": "2.0.0",
            "new_sha": "abc1234",
            "new_build_date": "2026-10-24T12:00:00Z",
        },
    )
    assert title == "Mise à jour installée"
    assert "image abc1234" in message
    assert "2026-10-24" not in message

    title2, message2 = notification_text(
        "system.updated",
        {
            "new_version": "2.0.0",
            "new_build_id": "deadbeef",
            "new_build_date": "vendredi 24/10/2026",
        },
    )
    assert "construite le vendredi 24/10/2026" in message2
    assert "deadbeef" not in message2
    assert "deadbeef" not in title2
