# Ported from parlaytracker@c3bd43c tests/unit/test_resolve.py. Changes: imports and fixture paths.
from datetime import UTC, datetime, timedelta

import pytest

from juju.ingest.resolve import normalize_name, same_game, same_team
from juju.tracker.markets import MARKET_SPORTS, NO_AUTO_CLOSING
from juju.tracker.models import Sport
from juju.tracker.resolve import MARKET_KEYS, SPORT_KEYS, same_player


def test_every_analysed_market_and_sport_has_a_key():
    assert set(MARKET_KEYS) == set(MARKET_SPORTS) - NO_AUTO_CLOSING
    assert set(SPORT_KEYS) == set(Sport)


@pytest.mark.parametrize(("a", "b"), [
    ("Luther Burden III", "Luther Burden"),
    ("D'Andre Swift", "DAndre Swift"),
    ("Amon-Ra St. Brown", "Amon Ra St Brown"),
    ("Kenneth Walker III", "Kenneth Walker"),
    ("Marvin Harrison Jr.", "Marvin Harrison"),
    ("Devonta Smith", "DeVonta Smith"),
])
def test_same_player(a, b):
    assert same_player(a, b)


@pytest.mark.parametrize(("a", "b"), [
    ("Jalen Hurts", "Jalen Carter"),
    ("Josh Allen", "Josh Jacobs"),
    ("A.J. Brown", "AJ Dillon"),
])
def test_different_players(a, b):
    assert not same_player(a, b)


def test_normalize_name():
    assert normalize_name("Dalvin Cook-Éa Jr.") == "dalvin cook ea jr"


def test_same_team_ignores_case_and_punctuation():
    assert same_team("Chicago Bears", "chicago bears")
    assert not same_team("Chicago Bears", "Chicago Bulls")


def test_same_game_needs_both_teams_and_a_close_start():
    start = datetime(2026, 9, 29, 0, 15, tzinfo=UTC)
    args = ("Chicago Bears", "Philadelphia Eagles", start)
    assert same_game(*args, "Chicago Bears", "Philadelphia Eagles", start + timedelta(hours=3))
    assert not same_game(*args, "Chicago Bears", "Philadelphia Eagles",
                         start + timedelta(hours=3, minutes=1))
    assert not same_game(*args, "Philadelphia Eagles", "Chicago Bears", start)  # home/away swapped
