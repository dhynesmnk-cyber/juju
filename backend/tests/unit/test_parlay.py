"""Same-game parlays: the pure rules (docs/GOALS.md section 5)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from juju.core.card import Outcome as O
from juju.core.markets import BY_KEY
from juju.core.odds import decimal_odds
from juju.core.parlay import (
    MAX_LEGS, choose_book, money, parlay_outcome, parse_legs, settled_returns,
)
from juju.core.t45 import Offer, Quote, Selection, Timing

AT = datetime(2026, 9, 28, 22, 0, tzinfo=UTC)


def test_legs_from_a_url():
    legs = parse_legs("p-3929630-player_rush_yds,p-4241478-player_receptions_alternate-4.5,"
                      "t-3-spreads")
    assert [leg.text for leg in legs] == [
        "p-3929630-player_rush_yds", "p-4241478-player_receptions_alternate-4.5", "t-3-spreads"]
    assert legs[1].threshold == D("4.5") and legs[2].team == "3"
    assert legs[0].market is BY_KEY["player_rush_yds"]


@pytest.mark.parametrize("text", [
    "p-1-player_rush_yds",                                   # one leg is not a parlay
    ",".join(f"p-{i}-player_rush_yds" for i in range(MAX_LEGS + 1)),
    "p-1-player_rush_yds,p-1-player_rush_yds",              # the same leg twice
    "p-1-player_rush_yds,p-2-not_a_market",
    "p-1-player_rush_yds,p-2-player_rush_yds_alternate",    # a ladder needs its threshold
    "p-1-player_rush_yds,p-2-player_rush_yds-99.5",         # and only a ladder has one
    "p-1-player_rush_yds,p-2-spreads",                      # a game market on a player
    "p-1-player_rush_yds,t-3-player_rush_yds",              # a player market on a team
    "p-1-player_rush_yds,t-3-alternate_spreads",            # only the main game lines
    "p-1-player_rush_yds,p-2-player_rush_yds_alternate-99.7",
    "p-1-player_rush_yds,x-2-spreads",
    "p-1-player_rush_yds;p-2-player_receptions",
])
def test_malformed_legs_are_refused(text):
    with pytest.raises(ValueError):
        parse_legs(text)


def quote(book: str, american: int, timing=Timing.ON_TIME, opposite: int | None = None) -> Quote:
    before = timedelta(minutes=1 if timing is Timing.ON_TIME else 60)
    return Quote(Offer(book, american, D("1.5"), AT, 1, "live", "ab" * 32,
                       opposite_american=opposite), timing, before)


def sel(*quotes: Quote) -> Selection:
    return Selection(quotes[0], None, list(quotes[1:])) if quotes else Selection(None)


CHAIN = ["hardrockbet", "draftkings", "fanduel"]


def test_one_book_for_every_leg_when_one_priced_them_all():
    legs = [sel(quote("hardrockbet", -110), quote("draftkings", -115)),
            sel(quote("draftkings", 150), quote("fanduel", 140))]
    book, quotes = choose_book(legs, CHAIN)
    assert book == "draftkings" and [q.offer.american for q in quotes] == [-115, 150]


def test_on_time_prices_first_then_early_ones():
    legs = [sel(quote("hardrockbet", -110, Timing.EARLY), quote("draftkings", -115)),
            sel(quote("hardrockbet", 150, Timing.EARLY), quote("draftkings", 140))]
    assert choose_book(legs, CHAIN)[0] == "draftkings"
    legs = [sel(quote("hardrockbet", -110, Timing.EARLY)),
            sel(quote("hardrockbet", 150, Timing.EARLY), quote("draftkings", 140))]
    assert choose_book(legs, CHAIN)[0] == "hardrockbet"


def test_with_no_common_book_each_leg_keeps_its_own():
    legs = [sel(quote("hardrockbet", -110)), sel(quote("fanduel", 150))]
    book, quotes = choose_book(legs, CHAIN)
    assert book is None and [q.offer.book for q in quotes] == ["hardrockbet", "fanduel"]
    book, quotes = choose_book([sel(quote("fanduel", 150)), sel()], CHAIN)
    assert book is None and quotes[1] is None


@pytest.mark.parametrize(("legs", "result"), [
    ([O.WON, O.WON], O.WON),
    ([O.WON, O.LOST], O.LOST),
    ([O.LOCKED, O.GONE], O.GONE),
    ([O.LIVE, O.LOST], O.LOST),                 # a lost leg decides it, whatever else
    ([O.LOCKED, O.LOCKED], O.LOCKED),
    ([O.LOCKED, O.LIVE], O.LIVE),
    ([O.WON, O.PUSH], O.WON),                   # a push drops out
    ([O.PUSH, O.VOID], O.PUSH),                 # every leg dropped out: stake back
    ([O.VOID, O.VOID], O.VOID),
    ([O.WON, O.NO_STAT_LINE], O.NO_STAT_LINE),  # never assumed zero or void
    ([O.LOCKED, O.UNAVAILABLE], O.UNAVAILABLE),
    ([O.LOCKED, O.UNTRACKED], O.UNTRACKED),
    ([O.PREGAME, O.PREGAME], O.PREGAME),
])
def test_the_parlay_outcome(legs, result):
    assert parlay_outcome(legs) is result


def test_money_multiplies_the_counted_legs():
    a, b, c = quote("draftkings", -110, opposite=-110), quote("draftkings", 150, opposite=-180), \
        quote("draftkings", 200)
    m = money([(O.WON, a), (O.WON, b)])
    assert m.returns == (D(10) * decimal_odds(-110) * decimal_odds(150)).quantize(D("0.01"))
    assert m.fair_returns is not None and m.fair_returns > m.returns and m.legs_counted == 2
    pushed = money([(O.WON, a), (O.PUSH, b)])
    assert pushed.returns == (D(10) * decimal_odds(-110)).quantize(D("0.01"))
    assert pushed.legs_counted == 1
    assert money([(O.WON, a), (O.WON, c)]).fair_returns is None  # a leg with one side only
    assert settled_returns(O.LOST, m) == D("0.00") and settled_returns(O.PUSH, m) == D(10)
    assert settled_returns(O.LOCKED, m) == m.returns and settled_returns(O.LIVE, m) is None
