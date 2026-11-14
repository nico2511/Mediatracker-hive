"""Ajouts UI : toute création ou réutilisation de fiche doit laisser followed=True."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_hive_t6 import _enable, _ruche, _seed_catalogue_row

from mediatracker.db.models import Media, MediaItem, Setting
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import HIVE_ENABLED_KEY

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)


def test_hive_catalogue_follow_reactivates_unfollowed_film(client, app):
    """Cause identifiée : POST /hive/catalogue/{key}/follow sur une fiche déjà présente mais non suivie."""
    login(client)
    _enable(client)
    cle = {"type": "film", "tmdb_id": 88123, "version": "HD+VFF"}
    work_key = _stable_work_key(cle)
    db = app.state.session_factory()
    try:
        db.merge(Setting(key=HIVE_ENABLED_KEY, value="true", is_secret=False, updated_at=NOW))
        _seed_catalogue_row(
            db, cle=cle, version="HD+VFF", sha="a" * 64, joignable=True, ruche_hex=_ruche(client).hex()
        )
        media = Media(
            type="film",
            title="Œuvre fictive",
            year=2099,
            tmdb_id=88123,
            followed=False,
            follow_origin="identification",
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, state="missing"))
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/api/v1/hive/catalogue/{work_key}/follow", headers=csrf(client))
    assert resp.status_code == 204, resp.text

    db = app.state.session_factory()
    try:
        row = db.scalar(select(Media).where(Media.tmdb_id == 88123, Media.type == "film"))
        assert row is not None
        assert row.followed is True
    finally:
        db.close()


def test_ui_follow_existing_fallback_on_follow_error(client, app, caplog):
    """Si follow() échoue, un repli minimal laisse followed=True (sans titre dans les logs)."""
    from mediatracker.api.v1 import hive_ui
    from mediatracker.core.follow import FollowError

    def _fail(*_args, **_kwargs):
        raise FollowError(422, "Requête", "Échec fictif de test")

    login(client)
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media = Media(
            type="film",
            title="Titre fictif repli",
            year=2098,
            tmdb_id=88124,
            followed=False,
            follow_origin="identification",
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, state="missing"))
        db.commit()
        media_id = media.id
    finally:
        db.close()

    original = hive_ui.follow
    hive_ui.follow = _fail
    try:
        with caplog.at_level("WARNING", logger="mediatracker.api.v1.hive_ui"):
            db = app.state.session_factory()
            try:
                row = db.get(Media, media_id)
                assert row is not None
                hive_ui._ui_follow_existing(db, row, settings, arm_source_poll=False)
                db.commit()
                db.refresh(row)
                assert row.followed is True
            finally:
                db.close()
    finally:
        hive_ui.follow = original

    assert any("repli minimal" in r.message.lower() for r in caplog.records)
    assert "Titre fictif" not in caplog.text
