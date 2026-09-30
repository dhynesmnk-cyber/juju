"""The T-45 selection rule at its boundaries (docs/GOALS.md section 4)."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from juju.core.t45 import Listing, NoPrice, Offer, Timing, select, target_time

KICKOFF = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
T45 = target_time(KICKOFF)  # 16:15
CHAIN = ["hardrockbet", "draftkings", "fanduel"]
_ids = iter(range(1, 10_000))


def snap(before: timedelta, books: dict[str, int | None]):
    """One snapshot `before` the T-45 target; `books` maps a book to its price, or None when it
    listed the market without our outcome."""
    sid = next(_ids)
    at = T45 - before
    offers = [Offer(b, a, D("73.5"), at, sid, "live", "sha") for b, a in books.items()
              if a is not None]
    listings = [Listing(b, sid, at) for b in books]
    return offers, listings


def run(*snaps, kickoff=KICKOFF, chain=CHAIN):
    offers = [o for s in snaps for o in s[0]]
    listings = [x for s in snaps for x in s[1]]
    return select(offers, listings, kickoff, chain)


def test_exactly_at_t45_is_on_time():
    sel = run(snap(timedelta(0), {"hardrockbet": -110}))
    assert sel.quote.timing is Timing.ON_TIME and sel.quote.offer.american == -110
    assert sel.quote.minutes_before_kickoff == 45


def test_a_snapshot_after_t45_is_never_used():
    sel = run(snap(-timedelta(milliseconds=1), {"hardrockbet": -110}))
    assert sel.quote is None and sel.reason is NoPrice.NO_SNAPSHOT


def test_five_minutes_early_is_on_time_and_a_second_more_is_early():
    assert run(snap(timedelta(minutes=5), {"draftkings": 120})).quote.timing is Timing.ON_TIME
    sel = run(snap(timedelta(minutes=5, seconds=1), {"draftkings": 120}))
    assert sel.quote.timing is Timing.EARLY
    assert sel.quote.before_target == timedelta(minutes=5, seconds=1)


def test_more_than_three_hours_early_is_no_price():
    sel = run(snap(timedelta(hours=3, seconds=1), {"hardrockbet": -110}))
    assert sel.quote is None and sel.reason is NoPrice.TOO_EARLY


def test_the_latest_snapshot_wins_never_an_average():
    sel = run(snap(timedelta(minutes=15), {"hardrockbet": -120}),
              snap(timedelta(minutes=2), {"hardrockbet": -105}))
    assert sel.quote.offer.american == -105


def test_the_chain_order_decides_the_book():
    sel = run(snap(timedelta(minutes=1), {"fanduel": 100, "draftkings": 105, "hardrockbet": 110}))
    assert sel.quote.offer.book == "hardrockbet"
    assert [q.offer.book for q in sel.others] == ["draftkings", "fanduel"]


def test_a_later_book_on_time_beats_an_earlier_book_that_is_only_early():
    sel = run(snap(timedelta(minutes=40), {"hardrockbet": 110}),
              snap(timedelta(minutes=1), {"draftkings": 105}))
    assert sel.quote.offer.book == "draftkings" and sel.quote.timing is Timing.ON_TIME


def test_early_is_used_when_nothing_is_on_time():
    sel = run(snap(timedelta(minutes=40), {"draftkings": 105}))
    assert sel.quote.offer.book == "draftkings" and sel.quote.timing is Timing.EARLY


def test_a_pulled_player_has_no_price_from_older_snapshots():
    # Listed at T-60, pulled at T-47: the older price is not used for that book.
    sel = run(snap(timedelta(minutes=15), {"hardrockbet": 110}),
              snap(timedelta(minutes=2), {"hardrockbet": None}))
    assert sel.quote is None and sel.reason is NoPrice.NOT_LISTED


def test_books_outside_the_chain_are_never_shown():
    sel = run(snap(timedelta(minutes=1), {"bovada": 150}))
    assert sel.quote is None and sel.reason is NoPrice.NOT_OFFERED


def test_a_moved_kickoff_reselects():
    # Captured for a 17:00 kickoff; the game moved to 20:00, so T-45 is 19:15 and the same
    # snapshot is now hours early.
    s = snap(timedelta(minutes=1), {"hardrockbet": -110})
    moved = run(s, kickoff=KICKOFF + timedelta(hours=3))
    assert moved.quote is None and moved.reason is NoPrice.TOO_EARLY
    later = run(s, kickoff=KICKOFF + timedelta(minutes=30))
    assert later.quote.timing is Timing.EARLY


def test_nothing_archived():
    assert run().reason is NoPrice.NO_SNAPSHOT
