"""Live odds on demand: the rules (docs/plans/live-chat.md). Pure: no I/O, no clock, no names.

(`core/live.py` is something else: the live scores.)

What a live answer is, and is not:
- It is the price a book posts *now*, read from a Postings list the caller built from one fresh
  Odds API reply. Nothing here reads the archive (`core/t45.py`), and nothing is interpolated.
- A line that isn't posted is never made up. The answer says so and gives the nearest posted
  lines on each side.
- The money is `core/odds.py`'s, on the same $10 stake as every card.
- The wording is a template. No model writes a number.
- Every answer is about one player or team (a parlay: two to six legs). The caller filters a
  market down to the subject before it gets here, so nothing in this module can hold a board.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from juju.core import odds
from juju.core.card import STAKE
from juju.core.parlay import MAX_LEGS, MIXED_BOOKS_NOTE, SGP_NOTE

__all__ = ["MAX_LEGS", "SGP_NOTE", "Posting", "Price", "Pick", "Answer", "Cost", "ParlayPrice",
           "answer_for", "american_text", "money_text", "price_parlay", "cost_of", "credit_state",
           "opposite_side", "bet_text", "lead_sentence", "missing_line_sentence",
           "others_sentence", "parlay_sentence"]

OVER, UNDER, YES, NO = "Over", "Under", "Yes", "No"
_OPPOSITE = {OVER: UNDER, UNDER: OVER, YES: NO, NO: YES}


def opposite_side(side: str) -> str | None:
    return _OPPOSITE.get(side)


@dataclass(frozen=True)
class Posting:
    """One outcome one book posts, from one reply."""
    book: str
    market: str               # The Odds API's market key
    name: str                 # "Over", "Under", "Yes", "No", or a team
    point: Decimal | None
    american: int
    last_update: datetime | None = None


@dataclass(frozen=True)
class Price:
    book: str
    american: int
    last_update: datetime | None = None

    @property
    def returns(self) -> Decimal:
        """What $10 returns, with the stake, if it hits."""
        return odds.payout(STAKE, odds.decimal_odds(self.american))


@dataclass(frozen=True)
class Pick:
    """One posted line, priced. `prices` is every book in the chain that posts it on this side,
    in chain order: the first is the book of record, the rest are listed beneath it. The best
    price is never the headline."""
    point: Decimal | None
    prices: tuple[Price, ...]
    opposite: Price | None    # the other side at the book of record, on this line

    @property
    def book(self) -> Price:
        return self.prices[0]

    @property
    def others(self) -> tuple[Price, ...]:
        return self.prices[1:]


@dataclass(frozen=True)
class Answer:
    picks: tuple[Pick, ...]   # the line asked for; or the nearest either side; or every line
    exact: bool               # the asked-for line is posted (also true when none was asked)
    asked: Decimal | None


def _order(chain: Sequence[str]) -> dict[str, int]:
    return {book: i for i, book in enumerate(chain)}


def answer_for(postings: Sequence[Posting], side: str | None, asked: Decimal | None,
               chain: Sequence[str]) -> Answer:
    """What the books post on `side` for one subject (a player, a team or the game total).

    `postings` are that subject's outcomes only, both sides. Books outside the chain are
    ignored (offshore books are never shown). With a line asked for, an exact match wins;
    otherwise the nearest posted line below and above. With none asked, every posted line.
    """
    order = _order(chain)
    mine = [p for p in postings if p.book in order]
    wanted = [p for p in mine if side is None or p.name == side]
    other_side = _OPPOSITE.get(side) if side else None
    points = sorted({p.point for p in wanted}, key=lambda x: (x is not None, x))

    def pick(point: Decimal | None) -> Pick:
        here = sorted((p for p in wanted if p.point == point), key=lambda p: order[p.book])
        seen: set[str] = set()
        prices = []
        for p in here:
            if p.book not in seen:
                seen.add(p.book)
                prices.append(Price(p.book, p.american, p.last_update))
        opposite = None
        if other_side is not None:
            first = prices[0].book
            opp = next((p for p in mine if p.name == other_side and p.point == point
                        and p.book == first), None)
            opposite = Price(opp.book, opp.american, opp.last_update) if opp else None
        return Pick(point, tuple(prices), opposite)

    if not points:
        return Answer((), False, asked)
    if asked is None:
        return Answer(tuple(pick(p) for p in points), True, None)
    if asked in points:
        return Answer((pick(asked),), True, asked)
    below = [p for p in points if p is not None and p < asked]
    above = [p for p in points if p is not None and p > asked]
    near = ([max(below)] if below else []) + ([min(above)] if above else [])
    return Answer(tuple(pick(p) for p in near), False, asked)


# --- Credits ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cost:
    """What a question will cost before it is asked: the fewest and the most credits, and how
    many of its markets are already in the 60-second cache (free)."""
    low: int
    high: int
    cached: int = 0

    @property
    def text(self) -> str:
        if self.high == 0:
            return "Free: answered from the last minute's prices."
        credit = lambda n: f"{n} credit" + ("" if n == 1 else "s")  # noqa: E731
        if self.low == 0:
            return f"Free unless the line isn't posted: then {credit(self.high)}."
        if self.low == self.high:
            return f"This will cost {credit(self.high)}."
        return f"This will cost {self.low} to {credit(self.high)}."


def cost_of(markets: Sequence[str], cached: set[str], alternates: Mapping[str, bool],
            per_call: int = 1) -> Cost:
    """`markets`: the main market of each distinct thing asked. `cached`: market keys the last
    minute already fetched. `alternates[key]`: whether the market has an alternate ladder that
    may be needed when the exact line isn't in it (one more credit, only then)."""
    fresh = [m for m in dict.fromkeys(markets) if m not in cached]
    low = len(fresh) * per_call
    extra = sum(per_call for m in dict.fromkeys(markets) if alternates.get(m, False))
    return Cost(low, low + extra, len(set(markets)) - len(fresh))


def credit_state(remaining: int | None, low_at: int) -> str:
    """"unknown" until The Odds API has said, then "ok", "low" or "out"."""
    if remaining is None:
        return "unknown"
    if remaining <= 0:
        return "out"
    return "low" if remaining <= low_at else "ok"


# --- Words -----------------------------------------------------------------------------------


def american_text(american: int) -> str:
    return f"+{american}" if american > 0 else f"{american}"


def money_text(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def _point(point: Decimal) -> str:
    return format(point.normalize(), "f")


def bet_text(label: str, subject: str | None, side: str | None, point: Decimal | None,
             *, yes_only: bool = False, spread: bool = False, moneyline: bool = False) -> str:
    """"Fannin Over 4.5 receptions", "Browns -3.5", "Chiefs to win", "Over 44.5 game total"."""
    who = subject or ""
    if moneyline:
        return f"{who} to win".strip()
    if spread and point is not None:
        return f"{who} {point:+}".strip()
    if yes_only:
        return f"{who} {label.lower()}".strip()
    line = f"{side} {_point(point)}" if side and point is not None else (side or "")
    return f"{who} {line} {label.lower()}".replace("  ", " ").strip()


def lead_sentence(bet: str, pick: Pick, names: Mapping[str, str]) -> str:
    """"Fannin Over 4.5 receptions pays +180 at DraftKings right now: $10 would return $28.00
    if it hits." Present tense: the price is today's, not a past price."""
    price = pick.book
    return (f"{bet} pays {american_text(price.american)} at {names.get(price.book, price.book)} "
            f"right now: $10 would return {money_text(price.returns)} if it hits.")


def others_sentence(pick: Pick, names: Mapping[str, str]) -> str | None:
    if not pick.others:
        return None
    listed = ", ".join(f"{names.get(p.book, p.book)} {american_text(p.american)}"
                       for p in pick.others)
    return f"Other books: {listed}."


def missing_line_sentence(asked: Decimal, answer: Answer, label: str, side: str | None,
                          names: Mapping[str, str]) -> str:
    """The line asked for isn't posted: say so, then the nearest lines that are."""
    if not answer.picks:
        return f"No book Juju follows posts {label.lower()} for this one right now."
    near = "; ".join(
        f"{side or ''} {_point(p.point) if p.point is not None else ''} pays "
        f"{american_text(p.book.american)} at {names.get(p.book.book, p.book.book)}".strip()
        for p in answer.picks)
    return f"{_point(asked)} isn't posted right now. The nearest lines: {near}."


# --- Parlays ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParlayPrice:
    book: str | None          # the one book that priced every leg; None: each leg its own
    legs: tuple[Price, ...]
    returns: Decimal
    notes: tuple[str, ...]


def price_parlay(picks: Sequence[Pick], chain: Sequence[str]) -> ParlayPrice | None:
    """Standard parlay maths (`core/odds.py`) on the live legs: at the first book in the chain
    that posts every leg, else each leg at its own book of record. None for fewer than two or
    more than six legs, or a leg with no price."""
    if not 2 <= len(picks) <= MAX_LEGS or any(not p.prices for p in picks):
        return None
    chosen_book: str | None = None
    legs: tuple[Price, ...] = ()
    for book in chain:
        prices = [next((x for x in p.prices if x.book == book), None) for p in picks]
        if all(x is not None for x in prices):
            chosen_book = book
            legs = tuple(x for x in prices if x is not None)
            break
    notes = [SGP_NOTE]
    if chosen_book is None:
        legs = tuple(p.book for p in picks)
        notes.append(MIXED_BOOKS_NOTE)
    decimal = odds.parlay_decimal(x.american for x in legs)
    return ParlayPrice(chosen_book, legs, odds.payout(STAKE, decimal), tuple(notes))


def parlay_sentence(count: int, parlay: ParlayPrice, names: Mapping[str, str]) -> str:
    at = (f" at {names.get(parlay.book, parlay.book)}" if parlay.book
          else " (each leg at its own book)")
    return (f"Your {count}-leg parlay pays about {money_text(parlay.returns)} on $10{at}, "
            f"if every leg hits.")
