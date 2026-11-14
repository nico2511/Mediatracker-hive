"""Season/episode parsing in release and filename tokens (invented titles only)."""

import pytest

from mediatracker.library.parse import (
    episode_bits_from_text,
    parse_release,
    parse_release_slot,
    release_matches_episode,
    slot_from_name,
)

_EPISODE_CASES = (
    ("Alpha S01E02 1080p", 1, 2, False, None),
    ("Alpha s1e2 WEB", 1, 2, False, None),
    ("Alpha 1x02 HDTV", 1, 2, False, None),
    ("Alpha S01E02E03", 1, 2, False, None),
    ("Alpha S01E02-E03", 1, 2, False, None),
    ("Alpha Saison 1 Épisode 2", 1, 2, False, None),
    ("Alpha Saison 1 Episode 2", 1, 2, False, None),
    ("Alpha 2019 1080p x264 5.1", None, None, False, None),
)

_PACK_CASES = (
    ("Alpha S01 Integrale 1080p", 1, None, True, None),
    ("Alpha Saison 1 Complete", 1, None, True, None),
    ("Alpha S01-S03 Complete", 1, None, True, 3),
    ("Alpha S02 720p", 2, None, True, None),
)


@pytest.mark.parametrize("label,season,episode,pack,season_end", _EPISODE_CASES)
def test_slot_from_name_and_parse_release_episode(label, season, episode, pack, season_end):
    stem = f"{label}.mkv"
    assert slot_from_name(stem) == (season, episode)
    parsed = parse_release(stem)
    assert parsed.season == season
    assert parsed.episode == episode
    assert parsed.season_pack is pack
    assert parsed.season_end == season_end


@pytest.mark.parametrize("label,season,episode,pack,season_end", _PACK_CASES)
def test_slot_from_name_ignores_pack_but_parse_release_keeps_it(label, season, episode, pack, season_end):
    stem = f"{label}.mkv"
    assert slot_from_name(stem) == (None, None)
    slot = parse_release_slot(stem)
    assert slot.season == season
    assert slot.episode == episode
    assert slot.pack is pack
    assert slot.season_end == season_end
    parsed = parse_release(stem)
    assert parsed.season_pack is pack


_MATCH_CASES = (
    ("Alpha S01E02.mkv", 1, 2, True),
    ("Alpha S01E02.mkv", 1, 3, False),
    ("Alpha S01E02E03.mkv", 1, 3, True),
    ("Alpha S01 Pack 1080p.mkv", 1, 9, True),
    ("Alpha S01 Pack 1080p.mkv", 2, 1, False),
    ("Alpha S01-S03 Complete.mkv", 2, 7, True),
    ("Alpha S01-S03 Complete.mkv", 4, 1, False),
    ("Alpha Saison 2 Integrale.mkv", 2, 1, True),
    ("Alpha Saison 2 Integrale.mkv", 1, 1, False),
    ("Alpha 2019 x264.mkv", 1, 1, False),
)


@pytest.mark.parametrize("name,season,episode,expected", _MATCH_CASES)
def test_release_matches_episode_table(name, season, episode, expected):
    assert release_matches_episode(name, season=season, episode=episode) is expected


def test_episode_bits_from_text_episode_only():
    assert episode_bits_from_text("Bloc — Épisode 5 TRUEFRENCH") == (None, 5)
    assert episode_bits_from_text("Bloc Saison 2 Épisode 5") == (2, 5)


def test_multi_episode_end_parsed():
    assert parse_release("Alpha S01E04-E06.mkv").episode_end == 6
    assert release_matches_episode("Alpha S01E04-E06.mkv", season=1, episode=5)
