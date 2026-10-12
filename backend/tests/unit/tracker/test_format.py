# Ported from parlaytracker@c3bd43c tests/app/test_live_page.py (its formatting cases) and
# tests/app/test_analytics_page.py (the figures' texts, which it read off the page). Changes: they
# call `juju/tracker/format.py`; the summaries are built on unsaved rows.
"""How the tracker says things: the words and numbers /me shows."""
from datetime import UTC, datetime
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from juju.tracker import format as fmt
from juju.tracker.analytics import Mean, summarize
from juju.tracker.live import LegView, State
from juju.tracker.models import (
    Event,
    Leg,
    LegResult,
    LegSource,
    MarketType,
    Slip,
    SlipType,
    Sport,
    Sportsbook,
    TeamSide,
)
from tests.unit.tracker.test_analytics import leg as leg_row


@pytest.mark.parametrize(("seconds", "text"), [
    (None, "never"), (0, "0 s ago"), (42, "42 s ago"), (119, "119 s ago"), (120, "2 min ago"),
    (3599, "59 min ago")])
def test_age_text(seconds, text):
    assert fmt.fmt_age(seconds) == text


@pytest.mark.parametrize(("value", "text"), [(None, "—"), (D("27"), "27"), (D("27.5"), "27.5"),
                                             (D("0.0"), "0")])
def test_value_text(value, text):
    assert fmt.fmt_value(value) == text


@pytest.mark.parametrize(("line", "text"), [(D("3.5"), "+3.5"), (D("-7"), "-7"), (D("0"), "0"),
                                            (None, "")])
def test_spread_text(line, text):
    assert fmt.fmt_line(line) == text


def test_money_is_dollars_when_placed_and_units_when_not():
    assert fmt.fmt_money(D("1234.5")) == "$1,234.50"
    assert fmt.fmt_money(D("1.5"), placed=False) == "1.50u"
    assert fmt.fmt_money(None) == "—"


def test_times_are_in_the_display_time_zone():
    moment = datetime(2026, 9, 29, 0, 15, tzinfo=UTC)
    assert fmt.fmt_time(moment, ZoneInfo("America/New_York")) == "Mon 28 Sep 20:15"
    assert fmt.fmt_time(moment, ZoneInfo("Europe/London")) == "Tue 29 Sep 01:15"


EVENT = Event(sport=Sport.NFL, espn_event_id="1", home_team="Chicago Bears",
              away_team="Philadelphia Eagles", home_espn_team_id="3", away_espn_team_id="21",
              start_time=datetime(2026, 10, 4, 17, 0, tzinfo=UTC))


def a_leg(market: MarketType, **fields) -> Leg:
    return Leg(event=EVENT, market_type=market, **fields)


@pytest.mark.parametrize(("leg", "text"), [
    (a_leg(MarketType.GAME_TOTAL, line=D("44.5"), american_odds=-110),
     "Philadelphia Eagles @ Chicago Bears · Game total o44.5 -110"),
    (a_leg(MarketType.ALT_SPREAD, side=TeamSide.HOME, line=D("-6.5"), american_odds=150),
     "Philadelphia Eagles @ Chicago Bears · Alt spread · Chicago Bears -6.5 +150"),
    (a_leg(MarketType.TEAM_TOTAL, side=TeamSide.AWAY, line=D("20.5")),
     "Philadelphia Eagles @ Chicago Bears · Team total · Philadelphia Eagles o20.5"),
    (a_leg(MarketType.PLAYER_RUSHING_YARDS, espn_athlete_id="7", player_name="Saquon Barkley",
           line=D("84.5"), american_odds=-115),
     "Philadelphia Eagles @ Chicago Bears · Rushing yards · Saquon Barkley o84.5 -115"),
    (a_leg(MarketType.PLAYER_RECEPTIONS, espn_athlete_id="7", line=D("5.5")),
     "Philadelphia Eagles @ Chicago Bears · Receptions · player 7 o5.5"),
    (a_leg(MarketType.OTHER, description="Bears moneyline", american_odds=120),
     "Philadelphia Eagles @ Chicago Bears · Bears moneyline +120"),
])
def test_leg_summary(leg, text):
    assert fmt.leg_summary(leg) == text


def test_slip_summary():
    book = Sportsbook(name="DraftKings")
    placed = Slip(slip_type=SlipType.SGP, american_odds=450, sportsbook=book, is_placed=True,
                  stake=D("10"), logged_by="Chris")
    assert fmt.slip_summary(placed) == ("Same-game parlay +450 · DraftKings · $10.00 to win · "
                                        "logged by Chris")
    idea = Slip(slip_type=SlipType.SINGLE, american_odds=-110, sportsbook=book, is_placed=False,
                logged_by="Ash")
    assert "Single -110 · DraftKings · Not placed · logged by Ash" == fmt.slip_summary(idea)


def view(leg: Leg, *, tracked=True, value=None, state=State.UNKNOWN, settled=None) -> LegView:
    return LegView(1, leg, tracked, value, state, settled, "Q2 8:42", LegSource.ESPN_WEB)


def test_standing_words():
    total = a_leg(MarketType.GAME_TOTAL, line=D("25.5"))
    spread = a_leg(MarketType.ALT_SPREAD, side=TeamSide.HOME, line=D("-6.5"))
    catches = a_leg(MarketType.PLAYER_RECEPTIONS, espn_athlete_id="7", line=D("5.5"))
    assert fmt.standing(view(total, value=D("27"), state=State.OVER)) == "27 · over"
    assert fmt.standing(view(total, value=D("20"), state=State.UNDER)) == "20 · under"
    assert fmt.standing(view(spread, value=D("7"), state=State.OVER)) == "7 · covering"
    assert fmt.standing(view(spread, value=D("3"), state=State.UNDER)) == "3 · not covering"
    assert fmt.standing(view(catches)) == "no stat line yet"
    assert fmt.standing(view(total)) == "waiting for a score"
    assert fmt.standing(view(total, tracked=False)) == fmt.NOT_TRACKED
    assert fmt.standing(view(total, settled=LegResult.WIN)) == "win"


def test_the_figures_texts():
    rows = [leg_row(result=LegResult.WIN) for _ in range(6)] + [
        leg_row(result=LegResult.LOSS) for _ in range(4)]
    stats = summarize("All legs", rows, 30)
    assert fmt.hit_rate_text(stats).startswith("60.0% (")
    assert fmt.evidence_text(stats) == "not yet evidence of an edge"
    assert fmt.clv_text(Mean(None, 0), "pp", 2) == "—"
    assert fmt.clv_text(Mean(3.5714, 1), "pp", 2) == "+3.57 pp (n 1)"
    assert fmt.signed(2.0, "pts") == "+2.0 pts" and fmt.pct(None) == "—"
