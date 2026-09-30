"""Same-game parlays (docs/GOALS.md sections 2 and 5, M4). Pure: no database access.

Standard parlay maths: the legs' decimal odds multiplied (`odds.parlay_decimal`). Books adjust
same-game parlays for correlation, so every parlay card says a real ticket would have paid less.

- **One book for every leg** when a book in the chain priced them all: the first such book,
  on-time prices before early ones. Otherwise each leg keeps its own book, and the card says so.
- **A pushed or voided leg drops out**, as at a book. If every leg does, the stake comes back.
- **A lost leg loses the parlay**, whatever the others do. A leg with no stat line leaves the
  parlay undecided: Juju never assumes a zero or a void.
- Every leg needs a price. A parlay with a leg Juju has no price for has no price either.
"""
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from juju.core import odds
from juju.core.card import STAKE, Outcome
from juju.core.markets import BY_KEY, Market, Scope
from juju.core.t45 import Quote, Selection, Timing

MAX_LEGS = 6
SGP_NOTE = ("Standard parlay maths. Books adjust same-game parlays for correlation, so a real "
            "ticket would have paid less.")
MIXED_BOOKS_NOTE = "No single book priced every leg, so each leg uses its own book's price."
TEAM_KEYS = frozenset({"h2h", "spreads", "totals", "team_totals"})

# A leg in a URL: "p-3929630-player_rush_yds", "p-3929630-player_rush_yds_alternate-99.5",
# "t-3-spreads". Legs are joined with commas.
_LEG = re.compile(r"^(?:p-(?P<athlete>\d{1,20})-(?P<pmarket>[a-z0-9_]{1,60})"
                  r"(?:-(?P<threshold>\d{1,3}(?:\.5)?))?"
                  r"|t-(?P<team>\d{1,10})-(?P<tmarket>[a-z_]{1,20}))$")


@dataclass(frozen=True)
class LegKey:
    market: Market
    athlete: str | None = None      # a player leg
    team: str | None = None         # a game or team leg: the side it backs
    threshold: Decimal | None = None  # an alternate line's Over, e.g. 99.5 for "100+"

    @property
    def text(self) -> str:
        if self.athlete is not None:
            tail = f"-{self.threshold}" if self.threshold is not None else ""
            return f"p-{self.athlete}-{self.market.key}{tail}"
        return f"t-{self.team}-{self.market.key}"


def parse_legs(text: str) -> list[LegKey]:
    """The legs of a parlay URL. Raises ValueError for anything malformed, a market that
    doesn't fit its kind of leg, a repeated leg, or more than MAX_LEGS."""
    parts = [p for p in text.split(",") if p]
    if not 2 <= len(parts) <= MAX_LEGS:
        raise ValueError(f"a parlay has 2 to {MAX_LEGS} legs")
    legs: list[LegKey] = []
    for part in parts:
        m = _LEG.match(part)
        if m is None:
            raise ValueError(f"not a leg: {part[:40]!r}")
        if m["athlete"]:
            market = BY_KEY.get(m["pmarket"])
            if market is None or market.scope is not Scope.PLAYER:
                raise ValueError(f"not a player market: {m['pmarket']}")
            threshold = Decimal(m["threshold"]) if m["threshold"] else None
            if market.alternate != (threshold is not None):
                raise ValueError("an alternate line needs its threshold, and only it has one")
            legs.append(LegKey(market, athlete=m["athlete"], threshold=threshold))
        else:
            if m["tmarket"] not in TEAM_KEYS:
                raise ValueError(f"not a game or team market: {m['tmarket']}")
            legs.append(LegKey(BY_KEY[m["tmarket"]], team=m["team"]))
    if len({leg.text for leg in legs}) != len(legs):
        raise ValueError("the same leg twice")
    return legs


def choose_book(selections: Sequence[Selection], chain: Sequence[str]
                ) -> tuple[str | None, list[Quote | None]]:
    """The book the parlay is priced at, and each leg's quote there; (None, each leg's own
    quote) when no book in the chain priced every leg."""
    by_book = [{q.offer.book: q for q in ([s.quote] if s.quote else []) + s.others}
               for s in selections]
    for on_time_only in (True, False):
        for book in chain:
            quotes = [b.get(book) for b in by_book]
            if all(q is not None and (q.timing is Timing.ON_TIME or not on_time_only)
                   for q in quotes):
                return book, quotes
    return None, [s.quote for s in selections]


_DROPS_OUT = frozenset({Outcome.PUSH, Outcome.VOID})


def parlay_outcome(legs: Sequence[Outcome]) -> Outcome:
    if Outcome.LOST in legs:
        return Outcome.LOST
    if Outcome.GONE in legs:
        return Outcome.GONE
    counted = [o for o in legs if o not in _DROPS_OUT]
    if not counted:
        return Outcome.VOID if all(o is Outcome.VOID for o in legs) else Outcome.PUSH
    if all(o is Outcome.WON for o in counted):
        return Outcome.WON
    for undecided in (Outcome.NO_STAT_LINE, Outcome.UNAVAILABLE, Outcome.UNTRACKED):
        if undecided in counted:
            return undecided
    if all(o in (Outcome.WON, Outcome.LOCKED) for o in counted):
        return Outcome.LOCKED
    if all(o is Outcome.PREGAME for o in counted):
        return Outcome.PREGAME
    return Outcome.LIVE


@dataclass(frozen=True)
class ParlayMoney:
    returns: Decimal               # what $10 returns if every counted leg wins
    fair_returns: Decimal | None   # without the margins, when every leg has both sides
    legs_counted: int


def money(legs: Sequence[tuple[Outcome, Quote]]) -> ParlayMoney:
    counted = [q for o, q in legs if o not in _DROPS_OUT]
    decimal = odds.parlay_decimal(q.offer.american for q in counted)
    fair = None
    if all(q.offer.opposite_american is not None for q in counted):
        fair_decimal = Decimal(1)
        for q in counted:
            assert q.offer.opposite_american is not None
            p = odds.no_vig(odds.implied_probability(q.offer.american),
                            odds.implied_probability(q.offer.opposite_american))
            fair_decimal *= Decimal(1) / Decimal(str(p))
        fair = odds.payout(STAKE, fair_decimal)
    return ParlayMoney(odds.payout(STAKE, decimal), fair, len(counted))


def settled_returns(outcome: Outcome, m: ParlayMoney) -> Decimal | None:
    match outcome:
        case Outcome.WON | Outcome.LOCKED:
            return m.returns
        case Outcome.LOST | Outcome.GONE:
            return Decimal("0.00")
        case Outcome.PUSH | Outcome.VOID:
            return STAKE
    return None
