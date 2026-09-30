"""The archive jobs on a fake clock (docs/GOALS.md section 4)."""
import gzip
import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.models import OddsSnapshot, PlayerNameMap, Price
from juju.worker.capture import SLOTS, CaptureT45, RepairGaps, due_slot
from tests.support import BARKLEY, KICKOFF, FakeOdds, seed_phi_chi

pytestmark = pytest.mark.db


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def at(minutes_before: float) -> Clock:
    return Clock(KICKOFF - timedelta(minutes=minutes_before))


def snapshots(engine):
    with Session(engine) as s:
        return [(x.slot, x.source) for x in s.scalars(
            select(OddsSnapshot).order_by(OddsSnapshot.id))]


@pytest.fixture
def game(db):
    with Session(db) as s:
        return seed_phi_chi(s, KICKOFF - timedelta(hours=5)).id


def test_due_slot_skips_missed_slots_and_stops_at_t45():
    assert due_slot(KICKOFF, KICKOFF - timedelta(hours=4), set()) is None
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=170), set()).name == "t180"
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=48), set()).name == "t49"
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=48), {"t49"}) is None
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=45, seconds=10), set()).name == "t45"
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=44, seconds=59), set()) is None
    assert [s.name for s in SLOTS] == ["t180", "t60", "t49", "t47", "t45"]


def test_a_full_pregame_run(db, game):
    odds, clock = FakeOdds(), at(180)
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=clock)
    for minutes in (180, 120, 60, 55, 49, 47, 45.5, 45.2, 44, 10):
        clock.now = KICKOFF - timedelta(minutes=minutes)
        job()
    assert [s for s, _ in snapshots(db)] == ["t180", "t60", "t49", "t47", "t45"]
    with Session(db) as s:
        snap = s.scalars(select(OddsSnapshot).where(OddsSnapshot.slot == "t45")).one()
        assert snap.observed_at == KICKOFF - timedelta(minutes=45.5)
        # provenance: the stored payload is exactly what came back, and its hash matches
        raw = gzip.decompress(snap.payload_gzip)
        assert hashlib.sha256(raw).hexdigest() == snap.payload_sha256
        barkley = s.scalars(select(Price).where(
            Price.snapshot_id == snap.id, Price.market_key == "player_rush_yds",
            Price.book == "draftkings", Price.outcome_name == "Over",
            Price.description == "Saquon Barkley")).one()
        assert (barkley.espn_athlete_id, barkley.point, barkley.american) == (BARKLEY, 73.5, -112)
        assert barkley.market_last_update is not None


def test_a_failed_slot_retries_until_t45_and_never_after(db, game):
    odds, clock = FakeOdds(fail=True), at(46)
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=clock)
    job()
    clock.now += timedelta(seconds=10)
    job()  # too soon to retry
    assert len(odds.calls) == 1
    clock.now += timedelta(seconds=20)
    job()
    assert len(odds.calls) == 2
    odds.fail = False
    clock.now = KICKOFF - timedelta(minutes=44, seconds=59)
    job()
    assert len(odds.calls) == 2 and snapshots(db) == []


def test_an_unknown_market_key_is_isolated_and_the_rest_captured(db, game):
    odds = FakeOdds(reject={"player_1st_td"})
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=at(47))
    assert job() >= 1
    assert job.rejections.keys == {"player_1st_td"}
    with Session(db) as s:
        stored = {m for (ms,) in s.execute(select(OddsSnapshot.markets)) for m in ms.split(",")}
    assert "player_1st_td" not in stored and "player_rush_yds" in stored


def test_the_reserve_protects_credits_except_for_the_t45_captures(db, game):
    odds = FakeOdds(quota_remaining=1010)
    CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=at(60))()
    assert odds.calls == []  # t60 is not a priority slot
    CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=at(46))()
    assert len(odds.calls) == 1


def test_player_names_are_matched_or_listed_never_guessed(db, game):
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(47))()
    with Session(db) as s:
        statuses = dict(s.execute(select(PlayerNameMap.raw_name, PlayerNameMap.status)).all())
        assert statuses["Saquon Barkley"] == "auto"
        unmatched = s.scalar(select(func.count()).select_from(Price).where(
            Price.market_key.like("player_%"), Price.espn_athlete_id.is_(None)))
        doubtful = [n for n, st in statuses.items() if st != "auto"]
        # every unmatched price belongs to a name that is listed as doubtful or unmapped
        assert (unmatched == 0) == (doubtful == [])


def test_repair_fills_a_game_with_no_on_time_snapshot(db, game):
    odds = FakeOdds(vendor_ts=KICKOFF - timedelta(minutes=47))
    RepairGaps(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, repair_days=14,
               clock=at(30))()
    assert snapshots(db) == [("repair", "historical")]
    assert odds.calls[0][0] == "historical"
    assert odds.calls[0][3] == KICKOFF - timedelta(minutes=45)  # asks for exactly T-45
    with Session(db) as s:
        snap = s.scalars(select(OddsSnapshot)).one()
        assert snap.observed_at == KICKOFF - timedelta(minutes=47)  # the vendor's own time
    RepairGaps(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, repair_days=14, clock=at(-60))()
    assert len(odds.calls) == 1  # already repaired


def test_repair_leaves_games_captured_on_time_alone(db, game):
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(47))()
    odds = FakeOdds()
    RepairGaps(db, odds, DEFAULT_BOOK_CHAIN, reserve=0, repair_days=14, clock=at(30))()
    assert odds.calls == []
