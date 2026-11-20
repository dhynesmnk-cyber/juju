"""`read_live`: the live chat's reading of a line (ingest/parse.py). `parse_text`, which the
archive uses, is unchanged: its own tests (test_parse.py) still hold."""
from decimal import Decimal

import pytest

from juju.ingest.parse import parse_text, read_live


@pytest.mark.parametrize("text, who, side, line, market", [
    # "4 catches" is four or more: Over 3.5
    ("Klein got 4 catches", "klein", "Over", "3.5", "player_receptions"),
    ("Fannin 5+ receptions", "fannin", "Over", "4.5", "player_receptions"),
    ("Fannin at least 5 catches", "fannin", "Over", "4.5", "player_receptions"),
    ("Metcalf 4.5 receptions", "metcalf", "Over", "4.5", "player_receptions"),
    ("Metcalf under 3.5 receptions", "metcalf", "Under", "3.5", "player_receptions"),
    ("Metcalf u3.5 receptions", "metcalf", "Under", "3.5", "player_receptions"),
    ("fannin 60 receiving yards", "fannin", "Over", "59.5", "player_reception_yds"),
    ("Josh Downs over 45.5 receiving yards", "josh downs", "Over", "45.5",
     "player_reception_yds"),
    ("game total over 44.5", "", "Over", "44.5", "totals"),
    ("Fannin anytime td", "fannin", "Over", None, "player_anytime_td"),
])
def test_what_is_asked(text, who, side, line, market):
    r = read_live(text)
    assert (r.who, r.side, r.parsed.market_key) == (who, side, market)
    assert r.line == (Decimal(line) if line else None)


def test_yards_with_no_kind_are_left_for_the_players_position():
    r = read_live("Josh Downs over 45.5 yards")
    assert r.parsed.market_key is None and r.yards_of_any_kind
    assert r.line == Decimal("45.5") and r.who == "josh downs"


@pytest.mark.parametrize("text, who, handicap", [
    ("Browns -3.5", "browns", "-3.5"), ("Chiefs +2.5", "chiefs", "2.5"),
])
def test_a_teams_handicap_is_a_spread(text, who, handicap):
    r = read_live(text)
    assert r.who == who and r.handicap == Decimal(handicap)
    assert r.parsed.market_key == "spreads" and r.line is None


def test_a_moneyline_has_no_number():
    r = read_live("49ers moneyline")
    assert r.parsed.market_key == "h2h" and r.who == "49ers"
    assert r.line is None and r.handicap is None


def test_the_archives_parser_is_untouched():
    p = parse_text("Klein got 4 catches")
    assert p.market_key == "player_receptions" and p.threshold is None
    assert read_live("Klein got 4 catches").parsed == p


def test_text_that_names_nothing_is_not_guessed():
    r = read_live("what's the line")
    assert r.parsed.market_key is None and r.line is None and not r.yards_of_any_kind
