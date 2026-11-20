"""Trois règles Hive : coexistence manuelle, samaritain, masquage Recherché."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from tests.conftest import write_sparse_video_file
from tests.test_hive_profile_attempt import _seed_catalogue_row
from tests.test_hive_t6 import NOW, _enable
from tests.test_hive_wanted_v3 import _add_cherche, _film_cle

from mediatracker.core.naming import library_alternate_destination
from mediatracker.core.pipeline.queue import _import_one, _keep_best
from mediatracker.core.quality_profile import ensure_default_profile
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveWantedVolunteer,
    LibraryFile,
    Media,
    MediaItem,
    QualityProfile,
    TrashEntry,
)
from mediatracker.hive.signal_wanted import list_wanted_requests, volunteer_poll_profile_chain
from mediatracker.hive.source_download import try_enqueue_hive_for_item

pytest_plugins = ["tests.test_hive_t1", "tests.test_hive_profile_attempt"]


def _ensure_hive(db, ruche_hex: str) -> None:
    from mediatracker.db.models import HiveMember, Setting
    from mediatracker.hive.identity import HIVE_ENABLED_KEY

    ensure_default_profile(db)
    db.merge(Setting(key=HIVE_ENABLED_KEY, value="true", is_secret=False, updated_at=NOW))
    db.merge(
        HiveMember(
            ruche_id=bytes.fromhex(ruche_hex),
            member_instance_id="hv1test-test-test-test-test-test-test",
            public_address="https://peer.exemple.test",
            joined_at=NOW,
        )
    )


def _film_item(db) -> tuple[Media, MediaItem]:
    media = Media(type="film", title="Œuvre démo", year=2098, tmdb_id=880001, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    return media, item


def test_explicit_hive_coexist_two_files_no_trash(client, app, db, settings, tmp_path, ruche_hex):
    _ensure_hive(db, ruche_hex)
    media, item = _film_item(db)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    occupant = movies / "Œuvre démo (2098) [tmdbid-880001]" / "Œuvre démo (2098).mkv"
    write_sparse_video_file(occupant, 50_000_000)
    item.file_path = str(occupant.resolve())
    item.downloaded = True
    item.state = "downloaded"
    db.add(
        LibraryFile(
            path=str(occupant.resolve()),
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            size=1,
        )
    )
    sha = "c" * 64
    cle = {"type": "movie", "tmdb_id": media.tmdb_id, "version": "1080p+vostfr"}
    _seed_catalogue_row(
        db,
        cle=cle,
        version="1080p+VOSTFR",
        sha=sha,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=48_000_000,
    )
    incoming = tmp_path / "incoming.mkv"
    write_sparse_video_file(incoming, 48_000_000)
    dl = Download(media_item_id=item.id, state="post_processing", error=str(incoming), created_at=NOW)
    db.add(dl)
    db.flush()
    db.add(
        DownloadHiveFetch(
            download_id=dl.id,
            sha256_hex=sha,
            sources_json="[]",
            ruche_id=bytes.fromhex(ruche_hex),
            file_id=b"\x01" * 16,
            origin_instance_id="peer",
            peer_base_url="https://peer.exemple.test",
            expected_size=48_000_000,
            requested_version_key="1080p+VOSTFR",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.commit()
    _import_one(db, dl, NOW, settings)
    db.commit()
    assert occupant.is_file()
    alt = library_alternate_destination(occupant, "1080p+VOSTFR")
    assert alt.is_file()
    assert item.file_path == str(occupant.resolve())
    assert db.scalar(select(TrashEntry).limit(1)) is None
    rows = list(db.scalars(select(LibraryFile).where(LibraryFile.media_item_id == item.id)).all())
    assert len(rows) == 2


def test_auto_hive_still_departages(client, db, settings, tmp_path):
    from mediatracker.core.library_video import better_video

    media, item = _film_item(db)
    old = tmp_path / "old.mkv"
    write_sparse_video_file(old, 50_000_000)
    item.file_path = str(old)
    incoming = tmp_path / "incoming.mkv"
    write_sparse_video_file(incoming, 40_000_000)
    dest = tmp_path / "dest.mkv"
    assert better_video(incoming, old, challenger_qualities=("1080p",), incumbent_qualities=("720p",))
    dl = Download(media_item_id=item.id, state="post_processing", created_at=NOW)
    db.add(dl)
    db.flush()
    db.add(
        DownloadHiveFetch(
            download_id=dl.id,
            sha256_hex="a" * 64,
            sources_json="[]",
            ruche_id=b"\x02" * 16,
            file_id=b"\x03" * 16,
            origin_instance_id="peer",
            peer_base_url="https://peer.exemple.test",
            expected_size=40_000_000,
            requested_version_key=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.commit()
    with patch("mediatracker.core.pipeline.queue._finish_download") as finish:
        finish.side_effect = lambda *a, **k: None
        _keep_best(db, dl, item, media, incoming, dest, old, NOW, settings)
    assert not old.is_file() or not incoming.is_file()


def test_volunteer_poll_uses_instance_default_only(db):
    ensure_default_profile(db)
    default = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)).limit(1))
    assert default is not None
    chain = volunteer_poll_profile_chain(db)
    assert chain is not None
    assert len(chain.profiles) == 1
    assert chain.profiles[0].quality_profile_id == default.id


def test_volunteer_orchestrator_passes_single_default_profile(client, db, settings, ruche_hex, monkeypatch):
    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll

    _enable(client)
    _ensure_hive(db, ruche_hex)
    media, item = _film_item(db)
    default = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)).limit(1))
    extra = db.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF").limit(1))
    if extra is None:
        extra = QualityProfile(
            name="720p VF",
            languages=["VFF"],
            min_resolution="HD",
            max_resolution="HD",
            exclude_keywords=[],
            is_default=False,
        )
        db.add(extra)
        db.flush()
    media.fallback_profile_ids = str(extra.id)
    db.add(
        HiveWantedVolunteer(
            ruche_id=bytes.fromhex(ruche_hex),
            work_key="wk",
            volunteer_origin="local",
            media_item_id=item.id,
            created_at=NOW,
        )
    )
    db.commit()
    seen: list = []

    def _hive_apply(*_a, **kwargs):
        seen.append(kwargs.get("single_profile"))
        return None

    monkeypatch.setattr("mediatracker.hive.source_poll.run_hive_follow_poll", _hive_apply)
    factory = db.get_bind()
    from sqlalchemy.orm import sessionmaker

    orchestrate_profile_first_poll(
        sessionmaker(bind=factory, expire_on_commit=False),
        settings,
        media.id,
        force=True,
        now=NOW,
    )
    assert len(seen) == 1
    assert seen[0].quality_profile_id == default.id


def test_wanted_hidden_when_slot_has_any_version(client, settings, tmp_path):
    _enable(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881010)
        _add_cherche(db, settings, origin="peerRecherche901", cle=cle)
        media = Media(type="film", title="Œuvre plateau", year=2090, tmdb_id=881010, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloaded")
        db.add(item)
        db.flush()
        alt = tmp_path / "Œuvre plateau (2090) - VOSTFR.mkv"
        write_sparse_video_file(alt, 10_000_000)
        db.add(
            LibraryFile(
                path=str(alt.resolve()),
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                size=10_000_000,
            )
        )
        db.commit()
        rows = list_wanted_requests(db, settings, now=NOW)
        assert not any(row["tmdb_id"] == 881010 for row in rows)
    finally:
        db.close()


def test_explicit_request_allowed_when_file_present(db, settings, ruche_hex):
    _ensure_hive(db, ruche_hex)
    media, item = _film_item(db)
    occupant = Path(settings.data_dir) / "media" / "Films" / "slot.mkv"
    write_sparse_video_file(occupant, 10_000_000)
    item.file_path = str(occupant)
    item.downloaded = True
    db.commit()
    result = try_enqueue_hive_for_item(
        db, settings, media=media, item=item, now=NOW, version_key="1080p+VFF"
    )
    assert result.detail != "Cette œuvre est déjà dans votre bibliothèque."
