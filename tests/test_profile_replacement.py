"""Proposition de remplacement repli → profil principal (sans réseau dédié)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from tests.conftest import csrf, login

from mediatracker.core.pipeline.queue import _keep_best
from mediatracker.core.profile_fallback import encode_media_fallback_profile_ids
from mediatracker.core.profile_replacement import (
    dismiss_proposal,
    file_incoming_for_replacement,
    on_download_terminal,
    scan_proposals_after_poll,
    start_replace,
)
from mediatracker.db.models import (
    Download,
    Link,
    Media,
    MediaItem,
    ProfileReplacementProposal,
    Provider,
    QualityProfile,
)
from mediatracker.db.session import commit_retry
from mediatracker.hive.hive_received_filing import file_verified_part

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _profiles(db):
    principal = QualityProfile(name="Profil principal démo", languages=["VFF"], max_resolution="1080p")
    repli = QualityProfile(name="Profil repli démo", languages=["VOSTFR"], max_resolution="1080p")
    db.add(principal)
    db.add(repli)
    db.flush()
    return principal, repli


def _film(db, principal, repli, *, keep=False) -> tuple[Media, MediaItem]:
    media = Media(
        type="film",
        title="Fiche démo repli",
        year=2022,
        followed=True,
        quality_profile_id=principal.id,
        fallback_profile_ids=encode_media_fallback_profile_ids([repli.id]),
        source_order="mock_a",
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        state="downloaded",
        downloaded=True,
        keep=keep,
        served_poll_quality_profile_id=repli.id,
    )
    db.add(item)
    db.flush()
    return media, item


def test_scan_creates_proposal_from_cached_link(client, db, settings, tmp_path):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    path = tmp_path / "fichier-demo.mkv"
    path.write_bytes(b"demo-bytes")
    item.file_path = str(path)
    prov = db.scalar(select(Provider).limit(1))
    assert prov is not None
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=prov.id,
            kind="ddl_hoster",
            url="http://cache-demo.example/page",
            external_id="cache-demo-ref",
            quality="1080p",
            language="VFF",
            size=1_500_000_000,
            hoster="1fichier",
        )
    )
    commit_retry(db)
    with patch("mediatracker.core.profile_replacement.FileStatusIndex.load") as load_idx:
        load_idx.return_value.for_item.return_value = "present"
        created = scan_proposals_after_poll(db, media, settings, now=NOW)
    assert created == 1
    row = db.scalar(
        select(ProfileReplacementProposal).where(
            ProfileReplacementProposal.media_item_id == item.id,
            ProfileReplacementProposal.state == "active",
        )
    )
    assert row is not None
    assert row.principal_profile_id == principal.id
    assert row.repli_profile_id == repli.id


def test_no_proposal_when_served_principal_or_keep(client, db, settings, tmp_path):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli, keep=True)
    path = tmp_path / "keep-demo.mkv"
    path.write_bytes(b"x")
    item.file_path = str(path)
    prov = db.scalar(select(Provider).limit(1))
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=prov.id,
            kind="ddl_hoster",
            url="http://cache-demo.example/k",
            external_id="keep-ref",
            quality="1080p",
            language="VFF",
            size=1_500_000_000,
            hoster="1fichier",
        )
    )
    commit_retry(db)
    with patch("mediatracker.core.profile_replacement.FileStatusIndex.load") as load_idx:
        load_idx.return_value.for_item.return_value = "present"
        assert scan_proposals_after_poll(db, media, settings, now=NOW) == 0

    media2, item2 = _film(db, principal, repli)
    item2.keep = False
    item2.served_poll_quality_profile_id = principal.id
    item2.file_path = str(tmp_path / "principal.mkv")
    Path(item2.file_path).write_bytes(b"y")
    db.add(
        Link(
            media_item_id=item2.id,
            provider_id=prov.id,
            kind="ddl_hoster",
            url="http://cache-demo.example/p",
            external_id="principal-ref",
            quality="1080p",
            language="VFF",
            size=1_500_000_000,
            hoster="1fichier",
        )
    )
    commit_retry(db)
    with patch("mediatracker.core.profile_replacement.FileStatusIndex.load") as load_idx:
        load_idx.return_value.for_item.return_value = "present"
        assert scan_proposals_after_poll(db, media2, settings, now=NOW) == 0


def test_dismiss_blocks_same_version(client, db, settings, tmp_path):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    item.file_path = str(tmp_path / "d.mkv")
    Path(item.file_path).write_bytes(b"z")
    prov = db.scalar(select(Provider).limit(1))
    link = Link(
        media_item_id=item.id,
        provider_id=prov.id,
        kind="ddl_hoster",
        url="http://cache-demo.example/d",
        external_id="dismiss-ref",
        quality="1080p",
        language="VFF",
        size=1_500_000_000,
        hoster="1fichier",
    )
    db.add(link)
    commit_retry(db)
    with patch("mediatracker.core.profile_replacement.FileStatusIndex.load") as load_idx:
        load_idx.return_value.for_item.return_value = "present"
        scan_proposals_after_poll(db, media, settings, now=NOW)
    assert dismiss_proposal(db, item, now=NOW)
    commit_retry(db)
    with patch("mediatracker.core.profile_replacement.FileStatusIndex.load") as load_idx:
        load_idx.return_value.for_item.return_value = "present"
        assert scan_proposals_after_poll(db, media, settings, now=NOW) == 0
    assert (
        db.scalar(
            select(ProfileReplacementProposal).where(
                ProfileReplacementProposal.media_item_id == item.id,
                ProfileReplacementProposal.state == "active",
            )
        )
        is None
    )


def test_replace_queues_download_and_old_stays_until_success(client, db, settings, tmp_path):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    old = tmp_path / "old.mkv"
    old.write_bytes(b"old")
    item.file_path = str(old)
    prov = db.scalar(select(Provider).limit(1))
    link = Link(
        media_item_id=item.id,
        provider_id=prov.id,
        kind="ddl_hoster",
        url="http://cache-demo.example/r",
        external_id="replace-ref",
        quality="1080p",
        language="VFF",
        size=1_500_000_000,
        hoster="1fichier",
    )
    db.add(link)
    db.flush()
    proposal = ProfileReplacementProposal(
        media_item_id=item.id,
        principal_profile_id=principal.id,
        repli_profile_id=repli.id,
        version_key=f"link:{prov.id}:replace-ref",
        link_id=link.id,
        state="active",
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(proposal)
    commit_retry(db)
    result = start_replace(db, settings, media, item, now=NOW)
    assert result.ok
    assert old.is_file()
    assert db.scalar(select(Download).where(Download.id == result.download_id)).forced is True


def test_keep_best_replacement_keeps_smaller_incoming(client, db, settings, tmp_path):
    from mediatracker.core.library_video import better_video

    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    old = tmp_path / "lib" / "old-big.mkv"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x" * 5000)
    item.file_path = str(old)
    incoming = tmp_path / "incoming-small.mkv"
    incoming.write_bytes(b"y" * 500)
    dest = tmp_path / "lib" / "dest.mkv"
    assert not better_video(
        incoming,
        old,
        challenger_qualities=(None,),
        incumbent_qualities=(None,),
    )
    dl = Download(media_item_id=item.id, state="post_processing", poll_quality_profile_id=principal.id)
    db.add(dl)
    db.flush()
    db.add(
        ProfileReplacementProposal(
            media_item_id=item.id,
            principal_profile_id=principal.id,
            repli_profile_id=repli.id,
            version_key="link:1:swap",
            state="replacing",
            replace_download_id=dl.id,
            old_file_path=str(old),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    commit_retry(db)
    with patch("mediatracker.core.pipeline.queue._finish_download") as finish:
        finish.side_effect = lambda *a, **k: None
        _keep_best(db, dl, item, media, incoming, dest, old, NOW, settings)
    assert not old.is_file()
    assert dest.is_file()
    assert dest.read_bytes() == b"y" * 500


def test_incoming_move_failure_restores_old(client, db, settings, tmp_path, monkeypatch):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    movies = Path(settings.data_dir) / "media" / "Films"
    movies.mkdir(parents=True, exist_ok=True)
    old = movies / "restore-old.mkv"
    old.write_bytes(b"keep-me")
    item.file_path = str(old)
    incoming = tmp_path / "in.mkv"
    incoming.write_bytes(b"new")
    dest = movies / "restore-new.mkv"

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr("mediatracker.core.naming.atomic_move", boom)
    kept = file_incoming_for_replacement(db, settings, media, item, incoming, dest, old, NOW)
    assert kept is None
    assert old.is_file()
    assert old.read_bytes() == b"keep-me"


def test_hive_attach_failure_deletes_orphan_download(client, db, settings, tmp_path, monkeypatch):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    item.file_path = str(tmp_path / "h.mkv")
    Path(item.file_path).write_bytes(b"h")
    proposal = ProfileReplacementProposal(
        media_item_id=item.id,
        principal_profile_id=principal.id,
        repli_profile_id=repli.id,
        version_key="hive:" + "a" * 64,
        hive_sha256_hex="a" * 64,
        state="active",
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(proposal)
    commit_retry(db)
    before = db.scalar(select(func.count()).select_from(Download))

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_adapters.pick_catalog_match_for_profile_attempt",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        "mediatracker.hive.download_aria2.attach_or_refresh_hive_fetch",
        lambda *a, **k: None,
    )
    result = start_replace(db, settings, media, item, now=NOW)
    assert not result.ok
    after = db.scalar(select(func.count()).select_from(Download))
    assert after == before


def test_stale_proposal_reactivated_without_duplicate(client, db, settings, tmp_path):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    item.file_path = str(tmp_path / "stale.mkv")
    Path(item.file_path).write_bytes(b"s")
    prov = db.scalar(select(Provider).limit(1))
    version_key = f"link:{prov.id}:stale-ref"
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=prov.id,
            kind="ddl_hoster",
            url="http://cache-demo.example/stale",
            external_id="stale-ref",
            quality="1080p",
            language="VFF",
            size=1_500_000_000,
            hoster="1fichier",
        )
    )
    stale = ProfileReplacementProposal(
        media_item_id=item.id,
        principal_profile_id=principal.id,
        repli_profile_id=repli.id,
        version_key=version_key,
        state="stale",
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(stale)
    commit_retry(db)
    stale_id = stale.id
    with patch("mediatracker.core.profile_replacement.FileStatusIndex.load") as load_idx:
        load_idx.return_value.for_item.return_value = "present"
        scan_proposals_after_poll(db, media, settings, now=NOW)
    db.refresh(stale)
    assert stale.id == stale_id
    assert stale.state == "active"
    assert (
        db.scalar(select(func.count()).select_from(ProfileReplacementProposal).where(
            ProfileReplacementProposal.media_item_id == item.id
        ))
        == 1
    )


def test_hive_replacement_files_incoming_over_occupant(client, db, settings, tmp_path):
    from mediatracker.core.library_video import better_video

    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    layout_root = Path(settings.data_dir) / "media" / "Films"
    layout_root.mkdir(parents=True, exist_ok=True)
    folder = layout_root / "Fiche démo repli (2022)"
    folder.mkdir(parents=True)
    occupant = folder / "Fiche démo repli (2022).mkv"
    occupant.write_bytes(b"big" * 2000)
    item.file_path = str(occupant)
    part = tmp_path / "incoming.mkv"
    part.write_bytes(b"small")
    assert not better_video(
        part,
        occupant,
        challenger_qualities=(None,),
        incumbent_qualities=(None,),
    )
    dl = Download(media_item_id=item.id, state="downloading", poll_quality_profile_id=principal.id)
    db.add(dl)
    db.flush()
    db.add(
        ProfileReplacementProposal(
            media_item_id=item.id,
            principal_profile_id=principal.id,
            repli_profile_id=repli.id,
            version_key="hive:abc",
            state="replacing",
            replace_download_id=dl.id,
            old_file_path=str(occupant),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    commit_retry(db)
    dest, err = file_verified_part(
        db,
        settings,
        part=part,
        media=media,
        item=item,
        fiche={"ext": ".mkv"},
        now=NOW,
        download=dl,
    )
    assert err is None
    assert dest is not None
    assert dest is not None and dest.read_bytes() == b"small"
    assert occupant.read_bytes() == b"small"


def test_finish_keeps_old_file_on_failure(client, db, settings, tmp_path):
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    old = tmp_path / "fail-old.mkv"
    old.write_bytes(b"old")
    item.file_path = str(old)
    dl = Download(media_item_id=item.id, state="failed", poll_quality_profile_id=principal.id)
    db.add(dl)
    db.flush()
    proposal = ProfileReplacementProposal(
        media_item_id=item.id,
        principal_profile_id=principal.id,
        repli_profile_id=repli.id,
        version_key="link:1:fail",
        state="replacing",
        replace_download_id=dl.id,
        old_file_path=str(old),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(proposal)
    commit_retry(db)
    on_download_terminal(db, settings, media, item, dl, success=False, new_dest=None, now=NOW)
    assert old.is_file()
    assert proposal.state == "active"


def test_api_dismiss_and_replace(client, app, db, settings, tmp_path):
    login(client)
    principal, repli = _profiles(db)
    media, item = _film(db, principal, repli)
    item.file_path = str(tmp_path / "api.mkv")
    Path(item.file_path).write_bytes(b"a")
    prov = db.scalar(select(Provider).limit(1))
    link = Link(
        media_item_id=item.id,
        provider_id=prov.id,
        kind="ddl_hoster",
        url="http://cache-demo.example/api",
        external_id="api-ref",
        quality="1080p",
        language="VFF",
        size=1_500_000_000,
        hoster="1fichier",
    )
    db.add(link)
    db.flush()
    db.add(
        ProfileReplacementProposal(
            media_item_id=item.id,
            principal_profile_id=principal.id,
            repli_profile_id=repli.id,
            version_key=f"link:{prov.id}:api-ref",
            link_id=link.id,
            state="active",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    commit_retry(db)
    dismiss = client.post(
        f"/api/v1/items/{item.id}/profile-replacement/dismiss",
        headers=csrf(client),
    )
    assert dismiss.status_code == 200
    replace = client.post(
        f"/api/v1/items/{item.id}/profile-replacement/replace",
        headers=csrf(client),
    )
    assert replace.status_code == 422