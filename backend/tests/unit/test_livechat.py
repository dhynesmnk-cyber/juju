"""The live chat's rules (core/livechat.py): which line, which book, what $10 returns."""
from decimal import Decimal

import pytest

from juju.core import livechat
from juju.core.livechat import Posting, Price, answer_for

CHAIN = ["hardrockbet", "draftkings", "fanduel"]
D = Decimal


def p(book, name, point, american, market="player_receptions"):
    return Posting(book, market, name, None if point is None else D(point), american)


POSTINGS = [
    p("draftkings", "Over", "3.5", -133), p("draftkings", "Under", "3.5", 104),
    p("hardrockbet", "Over", "3.5", -120), p("hardrockbet", "Under", "3.5", 100),
    p("fanduel", "Over", "3.5", -125),
    p("draftkings", "Over", "5.5", 339), p("draftkings", "Under", "5.5", -480),
    p("offshore", "Over", "3.5", 150),
]


def test_the_exact_line_wins_and_the_book_of_record_leads():
    a = answer_for(POSTINGS, "Over", D("3.5"), CHAIN)
    assert a.exact and len(a.picks) == 1
    pick = a.picks[0]
    assert pick.book.book == "hardrockbet" and pick.book.american == -120
    assert [x.book for x in pick.others] == ["draftkings", "fanduel"]
    # the other side, at the book of record, on the same line
    assert pick.opposite == Price("hardrockbet", 100)


def test_the_best_price_is_never_the_headline_and_offshore_books_are_dropped():
    pick = answer_for(POSTINGS, "Over", D("3.5"), CHAIN).picks[0]
    assert pick.book.american == -120            # not DraftKings' -133, FanDuel's -125
    assert "offshore" not in [x.book for x in pick.prices]  # +150 never shows


def test_a_missing_line_gives_the_nearest_each_side_never_a_made_up_one():
    a = answer_for(POSTINGS, "Over", D("4.5"), CHAIN)
    assert not a.exact
    assert [x.point for x in a.picks] == [D("3.5"), D("5.5")]
    assert answer_for(POSTINGS, "Over", D("9.5"), CHAIN).picks[0].point == D("5.5")
    assert answer_for(POSTINGS, "Over", D("0.5"), CHAIN).picks[0].point == D("3.5")


def test_no_line_asked_lists_every_posted_line():
    a = answer_for(POSTINGS, "Over", None, CHAIN)
    assert a.exact and [x.point for x in a.picks] == [D("3.5"), D("5.5")]


def test_nothing_posted_on_that_side_is_an_empty_answer():
    assert answer_for(POSTINGS, "Yes", None, CHAIN).picks == ()
    assert answer_for([], "Over", D("3.5"), CHAIN).picks == ()


def test_a_book_listing_a_line_twice_counts_once():
    dup = POSTINGS + [p("draftkings", "Over", "3.5", -999)]
    pick = answer_for(dup, "Over", D("3.5"), CHAIN).picks[0]
    assert [x.book for x in pick.prices].count("draftkings") == 1


@pytest.mark.parametrize("american, returns", [
    (180, "28.00"), (-120, "18.33"), (-110, "19.09"), (100, "20.00"), (339, "43.90"),
])
def test_ten_dollars_returns_what_core_odds_says(american, returns):
    assert Price("draftkings", american).returns == D(returns)


def test_the_words():
    pick = answer_for(POSTINGS, "Over", D("3.5"), CHAIN).picks[0]
    names = {"hardrockbet": "Hard Rock Bet", "draftkings": "DraftKings", "fanduel": "FanDuel"}
    bet = livechat.bet_text("Receptions", "DK Metcalf", "Over", D("3.5"))
    assert bet == "DK Metcalf Over 3.5 receptions"
    assert livechat.lead_sentence(bet, pick, names) == (
        "DK Metcalf Over 3.5 receptions pays -120 at Hard Rock Bet right now: "
        "$10 would return $18.33 if it hits.")
    assert livechat.others_sentence(pick, names) == "Other books: DraftKings -133, FanDuel -125."
    a = answer_for(POSTINGS, "Over", D("4.5"), CHAIN)
    assert livechat.missing_line_sentence(D("4.5"), a, "Receptions", "Over", names) == (
        "4.5 isn't posted right now. The nearest lines: Over 3.5 pays -120 at Hard Rock Bet; "
        "Over 5.5 pays +339 at DraftKings.")
    assert livechat.american_text(250) == "+250" and livechat.american_text(-110) == "-110"
    assert livechat.money_text(D("1234.5")) == "$1,234.50"


def test_other_kinds_of_bet_read_plainly():
    assert livechat.bet_text("Spread", "Cleveland Browns", None, D("2.5"), spread=True) == (
        "Cleveland Browns +2.5")
    assert livechat.bet_text("Moneyline", "Pittsburgh Steelers", None, None, moneyline=True) == (
        "Pittsburgh Steelers to win")
    assert livechat.bet_text("Game total", None, "Under", D("38.5")) == "Under 38.5 game total"
    assert livechat.bet_text("Anytime TD", "Jaylen Warren", "Yes", None, yes_only=True) == (
        "Jaylen Warren anytime td")


# --- Parlays ---------------------------------------------------------------------------------


def pick_at(book_prices: dict[str, int], point="3.5") -> livechat.Pick:
    postings = [p(b, "Over", point, a) for b, a in book_prices.items()]
    return answer_for(postings, "Over", D(point), CHAIN).picks[0]


def test_a_parlay_is_priced_at_one_book_when_one_priced_every_leg():
    a = pick_at({"hardrockbet": 100, "draftkings": 150})
    b = pick_at({"draftkings": 200, "fanduel": 100})  # Hard Rock didn't post this leg
    parlay = livechat.price_parlay([a, b], CHAIN)
    assert parlay is not None and parlay.book == "draftkings"
    assert [x.american for x in parlay.legs] == [150, 200]
    assert parlay.returns == D("75.00")              # 2.5 x 3.0 x $10
    assert livechat.SGP_NOTE in parlay.notes and len(parlay.notes) == 1


def test_a_parlay_with_no_common_book_uses_each_legs_own_and_says_so():
    a = pick_at({"hardrockbet": 100})
    b = pick_at({"fanduel": 200})
    parlay = livechat.price_parlay([a, b], CHAIN)
    assert parlay is not None and parlay.book is None
    assert parlay.returns == D("60.00")              # 2.0 x 3.0 x $10
    assert livechat.MIXED_BOOKS_NOTE in parlay.notes


def test_a_parlay_has_two_to_six_priced_legs():
    leg = pick_at({"hardrockbet": 100})
    assert livechat.price_parlay([leg], CHAIN) is None
    assert livechat.price_parlay([leg] * 7, CHAIN) is None
    assert livechat.price_parlay([leg] * 6, CHAIN) is not None
    empty = answer_for([], "Over", D("3.5"), CHAIN)
    assert empty.picks == ()


# --- Credits ---------------------------------------------------------------------------------


def test_the_cost_is_shown_before_a_question_is_asked():
    one = livechat.cost_of(["player_receptions"], set(), {"player_receptions": False})
    assert (one.low, one.high) == (1, 1) and one.text == "This will cost 1 credit."
    maybe = livechat.cost_of(["player_receptions"], set(), {"player_receptions": True})
    assert (maybe.low, maybe.high) == (1, 2) and maybe.text == "This will cost 1 to 2 credits."
    free = livechat.cost_of(["player_receptions"], {"player_receptions"}, {})
    assert free.high == 0 and free.cached == 1 and free.text.startswith("Free")
    many = livechat.cost_of(["a", "b", "a", "c"], {"b"}, {})  # a repeated market counts once
    assert (many.low, many.high, many.cached) == (2, 2, 1)


@pytest.mark.parametrize("remaining, state", [
    (None, "unknown"), (5000, "ok"), (2000, "low"), (1, "low"), (0, "out"), (-3, "out"),
])
def test_the_credit_state(remaining, state):
    assert livechat.credit_state(remaining, 2000) == state
