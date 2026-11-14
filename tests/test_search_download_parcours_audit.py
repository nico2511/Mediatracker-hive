"""Parcours recherche/téléchargement : ordre profil×source, sources factices, sans réseau."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import pytest
from tests.conftest import login

from mediatracker.core.profile_attempt import iter_profile_source_attempts
from mediatracker.core.profile_attempt_adapters import choose_link_for_profile_attempt
from mediatracker.core.profile_attempt_orchestration import ProfileChain, orchestrate_profile_first_poll
from mediatracker.core.quality_profile import Profile
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, ensure_1fichier_last, hoster_rank
from mediatracker.core.source_poll import PollReport
from mediatracker.db.models import Link, Media

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)

BUILTIN_SOURCES = ("wawacity", "free_telecharger", "hive", "comet", "torrentio")

Motif = Literal[
    "source_indisponible",
    "sans_resultat",
    "erreur",
    "erreur_403",
    "succes",
]
NextStep = Literal["suivant", "arret_succes"]


@dataclass(frozen=True)
class AttemptStep:
    profile: str
    source: str
    motif: Motif
    next_step: NextStep


def _profile(name: str, langs: tuple[str, ...]) -> Profile:
    return Profile(
        name=name,
        languages=langs,
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )


def _report(
    result: str,
    *,
    queued: int = 0,
    episodes: int = 0,
    message: str = "",
) -> PollReport:
    return PollReport(
        result=result,
        message=message,
        episodes=episodes,
        queued=queued,
        proposed=0,
        last_check_at=NOW,
        next_check_at=NOW,
    )


class _StubRemote:
    def __init__(self, type_name: str) -> None:
        self.type = type_name

    def close(self) -> None:
        return None


@dataclass
class SourceBehavior:
    open_ok: bool = True
    outcome: Literal["succes", "sans_resultat", "erreur", "erreur_403"] = "sans_resultat"


def _outcome_to_report(outcome: str) -> PollReport:
    if outcome == "succes":
        return _report("found", queued=1, episodes=1, message="ok")
    if outcome == "erreur_403":
        return _report("error", message="HTTP 403 interdit")
    if outcome == "erreur":
        return _report("error", message="panne fictive")
    return _report("nothing", message="vide")


def _motif_from_outcome(outcome: str) -> tuple[Motif, NextStep]:
    if outcome == "succes":
        return "succes", "arret_succes"
    if outcome == "erreur_403":
        return "erreur_403", "suivant"
    if outcome == "erreur":
        return "erreur", "suivant"
    return "sans_resultat", "suivant"


def _install_orchestrate_mocks(
    monkeypatch,
    *,
    chain: ProfileChain,
    source_order: tuple[str, ...],
    behaviors: dict[tuple[str, str], SourceBehavior],
    hive_enabled: bool,
    execution_log: list[AttemptStep],
    calls: list[tuple[str, str]],
    unavailable_sources: frozenset[str] = frozenset(),
) -> None:
    def open_remote(_db, _settings, type_name, **_k):
        if type_name in unavailable_sources:
            return None
        if type_name not in source_order or type_name == "hive":
            return None
        return 1, _StubRemote(type_name)

    def worker_remote(factory, settings, media_id, provider_id, source, moment, draw, **kw):
        profile = kw.get("single_profile")
        assert profile is not None
        key = (profile.name, source.type)
        calls.append(key)
        behavior = behaviors.get(key, SourceBehavior())
        motif, next_step = _motif_from_outcome(behavior.outcome)
        execution_log.append(AttemptStep(profile.name, source.type, motif, next_step))
        return _outcome_to_report(behavior.outcome)

    def hive_poll(_db, _media, _settings, **kw):
        profile = kw.get("single_profile")
        assert profile is not None
        key = (profile.name, "hive")
        calls.append(key)
        behavior = behaviors.get(key, SourceBehavior())
        motif, next_step = _motif_from_outcome(behavior.outcome)
        execution_log.append(AttemptStep(profile.name, "hive", motif, next_step))
        return _outcome_to_report(behavior.outcome)

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: chain,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: source_order,
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: hive_enabled)
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", worker_remote)
    monkeypatch.setattr("mediatracker.hive.source_poll.run_hive_follow_poll", hive_poll)
    monkeypatch.setattr("mediatracker.core.source_poll.close_source", lambda *_a, **_k: None)


def _run_media(client, source_order: tuple[str, ...] | None = None) -> int:
    login(client)
    factory = client.app.state.session_factory
    db = factory()
    try:
        kwargs = {"type": "series", "title": "Fictif audit", "year": 2020, "followed": True}
        if source_order is not None:
            kwargs["source_order"] = ",".join(source_order)
        media = Media(**kwargs)
        db.add(media)
        db.commit()
        return media.id
    finally:
        db.close()


def test_contract_profile_before_source_all_five():
    p1, p2 = _profile("Principal", ("VFF",)), _profile("Repli", ("VOSTFR",))
    order = BUILTIN_SOURCES
    labels = [(a.profile.name, a.source) for a in iter_profile_source_attempts((p1, p2), order)]
    assert labels == [
        ("Principal", "wawacity"),
        ("Principal", "free_telecharger"),
        ("Principal", "hive"),
        ("Principal", "comet"),
        ("Principal", "torrentio"),
        ("Repli", "wawacity"),
        ("Repli", "free_telecharger"),
        ("Repli", "hive"),
        ("Repli", "comet"),
        ("Repli", "torrentio"),
    ]


@pytest.mark.parametrize("source", BUILTIN_SOURCES)
def test_each_source_success_stops_on_principal(client, monkeypatch, source):
    chain = ProfileChain((_profile("Principal", ("VFF",)), _profile("Repli", ("VOSTFR",))))
    behaviors = {(name, src): SourceBehavior() for name in ("Principal", "Repli") for src in BUILTIN_SOURCES}
    behaviors[("Principal", source)] = SourceBehavior(outcome="succes")
    log: list[AttemptStep] = []
    calls: list[tuple[str, str]] = []
    _install_orchestrate_mocks(
        monkeypatch,
        chain=chain,
        source_order=BUILTIN_SOURCES,
        behaviors=behaviors,
        hive_enabled=True,
        execution_log=log,
        calls=calls,
    )
    media_id = _run_media(client, BUILTIN_SOURCES)
    report = orchestrate_profile_first_poll(
        client.app.state.session_factory,
        client.app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert report is not None and report.queued == 1
    stop = BUILTIN_SOURCES.index(source) + 1
    assert calls == [("Principal", s) for s in BUILTIN_SOURCES[:stop]]
    assert log[-1] == AttemptStep("Principal", source, "succes", "arret_succes")


@pytest.mark.parametrize("source", BUILTIN_SOURCES)
def test_each_source_no_result_then_repli_success(client, monkeypatch, source):
    chain = ProfileChain((_profile("Principal", ("VFF",)), _profile("Repli", ("VOSTFR",))))
    behaviors: dict[tuple[str, str], SourceBehavior] = {}
    for src in BUILTIN_SOURCES:
        behaviors[("Principal", src)] = SourceBehavior(outcome="sans_resultat")
        behaviors[("Repli", src)] = SourceBehavior(outcome="sans_resultat")
    behaviors[("Repli", source)] = SourceBehavior(outcome="succes")
    log: list[AttemptStep] = []
    calls: list[tuple[str, str]] = []
    _install_orchestrate_mocks(
        monkeypatch,
        chain=chain,
        source_order=BUILTIN_SOURCES,
        behaviors=behaviors,
        hive_enabled=True,
        execution_log=log,
        calls=calls,
    )
    media_id = _run_media(client, BUILTIN_SOURCES)
    report = orchestrate_profile_first_poll(
        client.app.state.session_factory,
        client.app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert report is not None and report.queued == 1
    assert calls[-1] == ("Repli", source)
    assert sum(1 for c in calls if c[0] == "Principal") == len(BUILTIN_SOURCES)


@pytest.mark.parametrize(
    ("source", "outcome"),
    [(src, out) for src in BUILTIN_SOURCES for out in ("erreur", "erreur_403", "sans_resultat")],
)
def test_each_source_failure_on_principal_alone(client, monkeypatch, source, outcome):
    chain = ProfileChain((_profile("Principal", ("VFF",)),))
    behaviors = {("Principal", source): SourceBehavior(outcome=outcome)}  # type: ignore[arg-type]
    log: list[AttemptStep] = []
    calls: list[tuple[str, str]] = []
    _install_orchestrate_mocks(
        monkeypatch,
        chain=chain,
        source_order=(source,),
        behaviors=behaviors,
        hive_enabled=source == "hive",
        execution_log=log,
        calls=calls,
    )
    media_id = _run_media(client, (source,))
    report = orchestrate_profile_first_poll(
        client.app.state.session_factory,
        client.app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert calls == [("Principal", source)]
    motif, next_step = _motif_from_outcome(outcome)
    assert log == [AttemptStep("Principal", source, motif, next_step)]
    assert report is not None
    if outcome == "sans_resultat":
        assert report.result == "nothing"
    else:
        assert report.result == "error"


@pytest.mark.parametrize("source", ("wawacity", "free_telecharger", "comet", "torrentio"))
def test_empty_url_skips_source_then_continues(client, monkeypatch, source):
    chain = ProfileChain((_profile("Principal", ("VFF",)), _profile("Repli", ("VOSTFR",))))
    fallback = "torrentio" if source == "wawacity" else "wawacity"
    behaviors = {("Principal", fallback): SourceBehavior(outcome="succes")}
    log: list[AttemptStep] = []
    calls: list[tuple[str, str]] = []
    order = (source, fallback)
    _install_orchestrate_mocks(
        monkeypatch,
        chain=chain,
        source_order=order,
        behaviors=behaviors,
        hive_enabled=False,
        execution_log=log,
        calls=calls,
        unavailable_sources=frozenset({source}),
    )
    media_id = _run_media(client, order)
    report = orchestrate_profile_first_poll(
        client.app.state.session_factory,
        client.app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert report is not None and report.queued == 1
    assert all(c[1] != source for c in calls)
    assert ("Principal", fallback) in calls


def test_error_on_first_source_continues_to_second(client, monkeypatch):
    chain = ProfileChain((_profile("Principal", ("VFF",)),))
    order = ("wawacity", "comet")
    behaviors = {
        ("Principal", "wawacity"): SourceBehavior(outcome="erreur_403"),
        ("Principal", "comet"): SourceBehavior(outcome="succes"),
    }
    log: list[AttemptStep] = []
    calls: list[tuple[str, str]] = []
    _install_orchestrate_mocks(
        monkeypatch,
        chain=chain,
        source_order=order,
        behaviors=behaviors,
        hive_enabled=False,
        execution_log=log,
        calls=calls,
    )
    media_id = _run_media(client, order)
    report = orchestrate_profile_first_poll(
        client.app.state.session_factory,
        client.app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert calls == [("Principal", "wawacity"), ("Principal", "comet")]
    assert log == [
        AttemptStep("Principal", "wawacity", "erreur_403", "suivant"),
        AttemptStep("Principal", "comet", "succes", "arret_succes"),
    ]
    assert report is not None and report.result == "found" and report.queued == 1


def test_hive_disabled_skips_without_call(client, monkeypatch):
    chain = ProfileChain((_profile("Principal", ("VFF",)),))
    behaviors = {("Principal", "comet"): SourceBehavior(outcome="succes")}
    log: list[AttemptStep] = []
    calls: list[tuple[str, str]] = []
    hive_poll_calls: list[str] = []

    def hive_poll(*_a, **kw):
        hive_poll_calls.append("hive")
        return _report("found", queued=1, episodes=1)

    _install_orchestrate_mocks(
        monkeypatch,
        chain=chain,
        source_order=("hive", "comet"),
        behaviors=behaviors,
        hive_enabled=False,
        execution_log=log,
        calls=calls,
    )
    monkeypatch.setattr("mediatracker.hive.source_poll.run_hive_follow_poll", hive_poll)
    media_id = _run_media(client, ("hive", "comet"))
    report = orchestrate_profile_first_poll(
        client.app.state.session_factory,
        client.app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert hive_poll_calls == []
    assert calls == [("Principal", "comet")]
    assert report is not None and report.queued == 1


def test_hoster_uptobox_blocked_and_1fichier_last():
    assert "uptobox" in BLOCKED_SOURCE_HOSTERS
    pref = ensure_1fichier_last(["rapidgator", "1fichier", "turbobit"])
    assert pref[-1] == "1fichier"
    assert hoster_rank("uptobox", pref) > hoster_rank("1fichier", pref)
    profile = _profile("Principal", ("VFF",))
    uptobox = Link(
        id=1,
        media_item_id=1,
        url="https://uptobox.com/x",
        kind="ddl_hoster",
        hoster="uptobox",
        priority=0,
        dead=False,
    )
    rapid = Link(
        id=2,
        media_item_id=1,
        url="https://rapidgator.net/y",
        kind="ddl_hoster",
        hoster="rapidgator",
        quality="HD",
        language="VFF",
        priority=0,
        dead=False,
    )
    chosen = choose_link_for_profile_attempt(pref, links=[uptobox, rapid], profile=profile)
    assert chosen is rapid
