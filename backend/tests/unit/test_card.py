"""Card outcomes and money (docs/GOALS.md section 5)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from juju.core.card import (
    Bet, GameState, Outcome, PlayerStat, decide, money, settled_returns,
)
from juju.core.enums import EventStatus as S
from juju.core.markets import BY_KEY

KICK = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
LIVE_NOW = KICK + timedelta(hours=1)


def game(status=S.IN_PROGRESS, home=14, away=7, final_at=None, polled=LIVE_NOW):
    return GameState(status, KICK, home, away, final_at, polled)


RUSH = Bet(BY_KEY["player_rush_yds"], D("73.5"))
ANYTIME = Bet(BY_KEY["player_anytime_td"], D("0.5"))


def stat(v):
    return PlayerStat(None if v is None else D(v))


def test_pregame():
    assert decide(RUSH, game(S.SCHEDULED), stat(None), KICK - timedelta(minutes=5)).outcome \
        is Outcome.PREGAME


def test_live_shows_what_is_still_needed():
    d = decide(RUSH, game(), stat(60), LIVE_NOW)
    assert (d.outcome, d.current, d.needed) == (Outcome.LIVE, 60, 14)


def test_beating_the_line_during_the_game_is_locked_not_won():
    assert decide(RUSH, game(), stat(74), LIVE_NOW).outcome is Outcome.LOCKED
    assert decide(ANYTIME, game(), stat(1), LIVE_NOW).outcome is Outcome.LOCKED


def test_an_overturned_play_goes_back_to_live():
    assert decide(ANYTIME, game(), stat(0), LIVE_NOW).outcome is Outcome.LIVE


def test_no_stat_line_is_never_zero_while_live():
    d = decide(RUSH, game(), stat(None), LIVE_NOW)
    assert d.outcome is Outcome.LIVE and d.current is None


def test_final_waits_ten_minutes_before_settling():
    final_at = LIVE_NOW
    assert decide(RUSH, game(S.FINAL, final_at=final_at), stat(60),
                  final_at + timedelta(minutes=9)).outcome is Outcome.LIVE
    assert decide(RUSH, game(S.FINAL, final_at=final_at), stat(60),
                  final_at + timedelta(minutes=10)).outcome is Outcome.LOST
    assert decide(RUSH, game(S.FINAL, final_at=final_at), stat(82),
                  final_at + timedelta(minutes=10)).outcome is Outcome.WON


def test_push_on_a_whole_line():
    bet = Bet(BY_KEY["player_receptions"], D("5"))
    settled = LIVE_NOW + timedelta(hours=3)
    assert decide(bet, game(S.FINAL, final_at=LIVE_NOW), stat(5), settled).outcome is Outcome.PUSH


def test_no_stat_line_at_the_end_is_its_own_outcome():
    settled = LIVE_NOW + timedelta(hours=3)
    assert decide(RUSH, game(S.FINAL, final_at=LIVE_NOW), stat(None), settled).outcome \
        is Outcome.NO_STAT_LINE


def test_a_stale_feed_is_unavailable_not_a_guess():
    stale = game(polled=LIVE_NOW - timedelta(minutes=6))
    assert decide(RUSH, stale, stat(60), LIVE_NOW).outcome is Outcome.UNAVAILABLE


def test_cancelled_games_void():
    assert decide(RUSH, game(S.CANCELLED), stat(None), LIVE_NOW).outcome is Outcome.VOID


def test_untracked_market():
    bet = Bet(BY_KEY["player_pass_longest_completion"], D("35.5"))
    assert decide(bet, game(), stat(None), LIVE_NOW).outcome is Outcome.UNTRACKED


@pytest.mark.parametrize(("scorer", "live", "final"), [
    ("me", Outcome.LOCKED, Outcome.WON),
    ("someone", Outcome.GONE, Outcome.LOST),
    (None, Outcome.LIVE, Outcome.LOST),
])
def test_first_touchdown(scorer, live, final):
    bet = Bet(BY_KEY["player_1st_td"], None)
    s = PlayerStat(None, scorer, "me")
    assert decide(bet, game(), s, LIVE_NOW).outcome is live
    assert decide(bet, game(S.FINAL, final_at=LIVE_NOW), s,
                  LIVE_NOW + timedelta(hours=1)).outcome is final


def test_game_markets_follow_the_score():
    total = Bet(BY_KEY["totals"], D("42.5"))
    assert decide(total, game(home=28, away=17), None, LIVE_NOW).outcome is Outcome.LOCKED
    spread = Bet(BY_KEY["spreads"], D("-3.5"), side_is_home=True)
    d = decide(spread, game(home=14, away=7), None, LIVE_NOW)
    assert (d.outcome, d.current) == (Outcome.LIVE, 7)
    settled = LIVE_NOW + timedelta(hours=3)
    assert decide(spread, game(S.FINAL, 17, 14, LIVE_NOW), None, settled).outcome is Outcome.LOST
    ml = Bet(BY_KEY["h2h"], None, side_is_home=False)
    assert decide(ml, game(S.FINAL, 17, 20, LIVE_NOW), None, settled).outcome is Outcome.WON


def test_money_with_and_without_fair_value():
    m = money(-110, -110)
    assert m.returns == D("19.09") and m.profit == D("9.09")
    assert m.fair_returns == D("20.00") and m.fair_probability == pytest.approx(0.5)
    yes_only = money(110, None)
    assert yes_only.returns == D("21.00") and yes_only.fair_returns is None


def test_what_ten_dollars_came_back_as():
    m = money(150, None)
    assert settled_returns(Outcome.WON, m) == D("25.00")
    assert settled_returns(Outcome.LOST, m) == D("0.00")
    assert settled_returns(Outcome.PUSH, m) == D("10.00")
    assert settled_returns(Outcome.LIVE, m) is None
