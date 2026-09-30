"""Which archived price a card shows (docs/GOALS.md section 4). Pure: no database access.

The rule, in full:
1. The target is kickoff minus 45:00, from the game's *current* kickoff time.
2. For each book, use its latest snapshot taken at or before the target in which that book
   listed the market. If that snapshot doesn't have the outcome (the player was pulled), the
   book has no price, even if an earlier snapshot had one.
3. Walk the book chain. The first book whose price is ON_TIME (at most 5 minutes before the
   target) wins. If there is none, the first EARLY one (at most 3 hours before) wins, flagged.
4. Otherwise there is no price, and the reason says why.

Prices are never interpolated, and a snapshot taken after the target is never used.
"""
import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

T_MINUS = timedelta(minutes=45)
ON_TIME_WITHIN = timedelta(minutes=5)
EARLY_WITHIN = timedelta(hours=3)


class Timing(enum.StrEnum):
    ON_TIME = "on_time"
    EARLY = "early"


class NoPrice(enum.StrEnum):
    NO_SNAPSHOT = "no_snapshot"          # nothing archived at or before T-45 for this game
    NOT_OFFERED = "not_offered"          # no book in the chain listed this market
    NOT_LISTED = "not_listed"            # the market was up, but not this player or line
    TOO_EARLY = "too_early"              # only prices from more than 3 hours before T-45


@dataclass(frozen=True)
class Offer:
    """One book's price for the outcome in one snapshot."""
    book: str
    american: int
    point: Decimal | None
    observed_at: datetime
    snapshot_id: int
    source: str                       # "live" or "historical"
    payload_sha256: str
    market_last_update: datetime | None = None
    opposite_american: int | None = None  # the other side in the same snapshot, if listed


@dataclass(frozen=True)
class Listing:
    """A snapshot in which a book listed the market (whether or not it had our outcome)."""
    book: str
    snapshot_id: int
    observed_at: datetime


@dataclass(frozen=True)
class Quote:
    offer: Offer
    timing: Timing
    before_target: timedelta  # how long before T-45 the price was observed

    @property
    def minutes_before_kickoff(self) -> int:
        return int((T_MINUS + self.before_target).total_seconds() // 60)


@dataclass(frozen=True)
class Selection:
    quote: Quote | None
    reason: NoPrice | None = None
    # Every other book's price at its own latest snapshot, for the "other books" line.
    others: list[Quote] = field(default_factory=list)


def target_time(kickoff: datetime) -> datetime:
    return kickoff - T_MINUS


def select(offers: Iterable[Offer], listings: Iterable[Listing], kickoff: datetime,
           chain: Sequence[str]) -> Selection:
    target = target_time(kickoff)
    offers = [o for o in offers if o.observed_at <= target]
    listings = [x for x in listings if x.observed_at <= target]
    if not listings and not offers:
        return Selection(None, NoPrice.NO_SNAPSHOT)

    # Each book's latest snapshot (at or before the target) that listed the market.
    latest: dict[str, Listing] = {}
    for x in listings:
        held = latest.get(x.book)
        if held is None or (x.observed_at, x.snapshot_id) > (held.observed_at, held.snapshot_id):
            latest[x.book] = x
    by_book: dict[str, Quote] = {}
    for o in offers:
        listing = latest.get(o.book)
        if listing is None or o.snapshot_id != listing.snapshot_id:
            continue
        before = target - o.observed_at
        if before > EARLY_WITHIN:
            continue
        timing = Timing.ON_TIME if before <= ON_TIME_WITHIN else Timing.EARLY
        by_book[o.book] = Quote(o, timing, before)

    order = {book: i for i, book in enumerate(chain)}
    in_chain = sorted((q for b, q in by_book.items() if b in order),
                      key=lambda q: order[q.offer.book])
    chosen = (next((q for q in in_chain if q.timing is Timing.ON_TIME), None)
              or next(iter(in_chain), None))
    if chosen is None:
        if not any(b in order for b in latest):
            return Selection(None, NoPrice.NOT_OFFERED)
        too_early = any(o.book in order and latest.get(o.book)
                        and o.snapshot_id == latest[o.book].snapshot_id
                        for o in offers)
        return Selection(None, NoPrice.TOO_EARLY if too_early else NoPrice.NOT_LISTED)
    others = [q for q in in_chain if q is not chosen]
    return Selection(chosen, None, others)
