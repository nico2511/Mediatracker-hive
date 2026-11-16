"""API Réglages : test des sources sans lancer de téléchargement."""

from tests.conftest import csrf, login

from mediatracker.core.source_probe import parse_probe_query


def test_parse_probe_query_slot():
    target = parse_probe_query("Série Démo S01E03", imdb_id="tt0000001")
    assert target.title == "Série Démo"
    assert target.media_type == "series"
    assert target.season == 1
    assert target.episode == 3
    assert target.imdb_id == "tt0000001"


def test_probe_sources_endpoint_survives_stub_media_without_fiche_prefs(client, app, monkeypatch):
    """POST probe : legacy_comet_context ne doit pas lever ValidationError (quality/language None)."""
    login(client)

    def noop_probe(_db, _settings, name, _media, _item, **_kwargs):
        from mediatracker.core.source_probe import SourceProbeRow

        return SourceProbeRow(name, name.capitalize(), "skipped", 0, "test", 0)

    monkeypatch.setattr("mediatracker.core.source_probe._probe_one", noop_probe)
    response = client.post(
        "/api/v1/settings/sources/probe",
        json={"query": "Film Démo"},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["results"], list)


def test_probe_sources_endpoint_returns_ordered_rows(client, app, monkeypatch):
    login(client)

    from mediatracker.core.source_probe import ProbeRunResult, SourceProbeRow

    def fake_probe(_db, _settings, target, **_kwargs):
        assert target.title == "Film Démo"
        order = ["comet", "wawacity"]
        return ProbeRunResult(
            [
                SourceProbeRow(
                    source=name,
                    label=name.capitalize(),
                    issue="skipped",
                    link_count=0,
                    reason="test",
                    duration_ms=1,
                )
                for name in order
            ],
            None,
        )

    monkeypatch.setattr("mediatracker.api.v1.settings.probe_download_sources", fake_probe)
    response = client.post(
        "/api/v1/settings/sources/probe",
        json={"query": "Film Démo"},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    sources = [row["source"] for row in response.json()["results"]]
    assert sources == ["comet", "wawacity"]
