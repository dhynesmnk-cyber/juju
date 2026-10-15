"""The archive jobs on a fake clock (docs/GOALS.md section 4)."""
import gzip
import hashlib
from datetime import datetime, timedelta

from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.budget import FULL_COST, NO_CREDITS_NOTE
from juju.core.markets import ALL_KEYS, CAPTURE_PRIORITY, MAIN_KEYS
from juju.core.models import Game, OddsSnapshot, Player, PlayerNameMap, Price
from juju.ingest import espn
from juju.ingest.odds_api import OddsResponse
from juju.worker.archive import remap_prices, remap_recent
from juju.worker.capture import SLOTS, CaptureT45, RepairGaps, due_slot
from juju.worker.__main__ import at_startup
from juju.worker.schedule import SyncRosters, upsert_roster
from tests.support import BARKLEY, FIXTURES, HURTS, KICKOFF, FakeOdds, load, seed_phi_chi

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
    """Two captures, the owner's choice on 2026-10-15: T-3h, and T-45 from T-46 (so a failure
    can be retried) until T-45:00."""
    assert due_slot(KICKOFF, KICKOFF - timedelta(hours=4), set()) is None
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=170), set()).name == "t180"
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=48), {"t180"}) is None
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=46), {"t180"}).name == "t45"
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=46), set()).name == "t45"  # not t180
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=45, seconds=10), set()).name == "t45"
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=45, seconds=10), {"t45"}) is None
    assert due_slot(KICKOFF, KICKOFF - timedelta(minutes=44, seconds=59), set()) is None
    assert [s.name for s in SLOTS] == ["t180", "t45"]


def test_a_full_pregame_run(db, game):
    odds, clock = FakeOdds(), at(180)
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=clock)
    for minutes in (180, 120, 60, 55, 49, 47, 46, 45.5, 45.2, 44, 10):
        clock.now = KICKOFF - timedelta(minutes=minutes)
        job()
    assert [s for s, _ in snapshots(db)] == ["t180", "t45"]
    assert [c[2] for c in odds.calls] == [MAIN_KEYS, ALL_KEYS]
    with Session(db) as s:
        assert s.get(Game, game).capture_plan == "full"
        snap = s.scalars(select(OddsSnapshot).where(OddsSnapshot.slot == "t45")).one()
        assert snap.observed_at == KICKOFF - timedelta(minutes=46)
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


# --- The credit plan (core/budget.py) ---------------------------------------------------------
# KICKOFF is 2026-09-29 00:15Z; with credits resetting on the 1st, about 2 days and so 5 games'
# worth are left when it is captured.

def test_short_credits_drop_the_t3h_check_then_markets(db, game):
    odds, clock = FakeOdds(quota_remaining=30), at(180)  # 6 credits a game
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=0, clock=clock)
    for minutes in (180, 60, 46, 45.5, 44):
        clock.now = KICKOFF - timedelta(minutes=minutes)
        job()
    assert [s for s, _ in snapshots(db)] == ["t45"]
    assert odds.calls[0][2] == CAPTURE_PRIORITY[:6]
    with Session(db) as s:
        g = s.get(Game, game)
        assert (g.capture_plan, g.capture_note) == ("lean", None)
        snap = s.scalars(select(OddsSnapshot)).one()
        assert snap.markets.split(",") == list(CAPTURE_PRIORITY[:6])


def test_no_credits_left_records_why_and_calls_nothing(db, game):
    odds, clock = FakeOdds(quota_remaining=4), at(46)
    CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=0, clock=clock)()
    assert odds.calls == [] and snapshots(db) == []
    with Session(db) as s:
        g = s.get(Game, game)
        assert (g.capture_plan, g.capture_note) == ("none", NO_CREDITS_NOTE)


def test_a_game_keeps_the_plan_it_was_captured_under(db, game):
    odds, clock = FakeOdds(), at(46)
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=0, clock=clock)
    job()
    odds.quota_remaining = 0  # spent elsewhere, after this game's T-45
    clock.now = KICKOFF - timedelta(minutes=45, seconds=20)
    job()
    with Session(db) as s:
        assert s.get(Game, game).capture_plan == "full"


class SundayOdds(FakeOdds):
    """The recorded odds, listed at each game's own kickoff (the recording's would move it)."""
    kickoffs: dict = {}

    def event_odds(self, event_id, markets, bookmakers):
        r = super().event_odds(event_id, markets, bookmakers)
        odds = r.odds.model_copy(update={"commence_time": self.kickoffs[event_id]})
        return OddsResponse(odds, r.raw, r.cost, r.vendor_ts)


def _sunday(session: Session) -> list[int]:
    """Fourteen games on Sunday 2026-10-18: nine at 1 pm ET, four at 4:25, one at 8:20."""
    starts = (["17:00"] * 9) + (["20:25"] * 4) + ["00:20"]
    ids = []
    for i, hhmm in enumerate(starts):
        day = "2026-10-19" if hhmm == "00:20" else "2026-10-18"
        g = Game(home_team=f"Home {i}", away_team=f"Away {i}", odds_event_id=f"event-{i}",
                 commence_time=datetime.fromisoformat(f"{day}T{hhmm}:00+00:00"))
        session.add(g)
        session.flush()
        ids.append(g.id)
    session.commit()
    return ids


@pytest.mark.parametrize("quota, plan", [(20_000, "full"), (500, "lean")])
def test_a_simulated_sunday_stays_within_the_credits(db, quota, plan):
    """Every game gets its T-45 price, whatever the credits; a full month's worth buys both
    captures, a free key's 500 a lean T-45 for each."""
    with Session(db) as s:
        ids = _sunday(s)
        SundayOdds.kickoffs = dict(s.execute(select(Game.odds_event_id, Game.commence_time)).all())
    odds = SundayOdds(quota_remaining=quota)
    clock = Clock(datetime.fromisoformat("2026-10-18T13:00:00+00:00"))
    job = CaptureT45(db, odds, DEFAULT_BOOK_CHAIN, reserve=1000, clock=clock)
    while clock.now < datetime.fromisoformat("2026-10-19T01:00:00+00:00"):
        job()
        clock.now += timedelta(minutes=1)
    spent = quota - odds.quota_remaining
    with Session(db) as s:
        rows = s.execute(select(OddsSnapshot.game_id, OddsSnapshot.slot)).all()
        plans = {s.get(Game, i).capture_plan for i in ids}
    assert {i for i, slot in rows if slot == "t45"} == set(ids)
    assert plans == {plan}
    if plan == "full":
        assert {i for i, slot in rows if slot == "t180"} == set(ids)
        assert spent <= len(ids) * FULL_COST
    else:
        assert not any(slot == "t180" for _, slot in rows)
        assert spent <= quota and len(odds.calls) == len(ids)


def test_capture_runs_its_hook_before_each_capture(db, game):
    seen = []
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(46),
               before=lambda session, g: seen.append(g.id))()
    assert seen == [game]


# --- Player names matched again when the rosters change (worker/archive.py) -------------------

class RosterRouter:
    """Serves the recorded rosters, as SyncRosters asks ESPN for them."""
    def roster(self, team, max_wait=0.0):
        path = FIXTURES / "espn" / f"nfl_roster_{team}.json"
        return SimpleNamespace(value=espn.parse_roster(load(path)))


def _forget_rosters(db) -> None:
    with Session(db) as s:
        s.execute(delete(Player))
        s.commit()


def _unlinked(db) -> int:
    with Session(db) as s:
        return s.scalar(select(func.count()).select_from(Price).where(
            Price.market_key.like("player_%"), Price.espn_athlete_id.is_(None)))


def _status(db, name: str) -> str:
    with Session(db) as s:
        return s.scalar(select(PlayerNameMap.status).where(PlayerNameMap.raw_name == name))


def test_prices_captured_before_the_rosters_are_linked_when_they_arrive(db, game):
    _forget_rosters(db)
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(46))()
    before = _unlinked(db)
    assert before > 0 and _status(db, "Saquon Barkley") == "unmapped"
    assert SyncRosters(db, RosterRouter(), clock=at(40))() == 2
    assert _status(db, "Saquon Barkley") == "auto"
    with Session(db) as s:
        barkley = s.scalars(select(Price).where(
            Price.market_key == "player_rush_yds", Price.book == "draftkings",
            Price.outcome_name == "Over", Price.description == "Saquon Barkley")).one()
        assert barkley.espn_athlete_id == BARKLEY and barkley.american == -112  # the price as is
        still = {n for (n,) in s.execute(select(PlayerNameMap.raw_name).where(
            PlayerNameMap.status != "auto"))}
        left = {d for (d,) in s.execute(select(Price.description).where(
            Price.market_key.like("player_%"), Price.espn_athlete_id.is_(None)))}
    assert _unlinked(db) < before and left <= still  # only names that still don't match


def test_every_unmatched_name_is_tried_again_not_just_the_first(db, game):
    _forget_rosters(db)
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(180))()
    assert _status(db, "Saquon Barkley") == _status(db, "Jalen Hurts") == "unmapped"
    with Session(db) as s:  # the rosters arrive by another way: no re-link yet
        for team in ("21", "3"):
            roster = espn.parse_roster(load(FIXTURES / "espn" / f"nfl_roster_{team}.json"))
            upsert_roster(s, team, roster, KICKOFF - timedelta(minutes=50))
        s.commit()
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(46))()
    assert _status(db, "Saquon Barkley") == _status(db, "Jalen Hurts") == "auto"
    with Session(db) as s:
        t45 = s.scalars(select(OddsSnapshot).where(OddsSnapshot.slot == "t45")).one()
        hurts = s.scalars(select(Price).where(Price.snapshot_id == t45.id,
                                              Price.description == "Jalen Hurts")).all()
    assert hurts and all(p.espn_athlete_id == HURTS for p in hurts)


def test_remap_prices_links_the_earlier_snapshots_too(db, game):
    _forget_rosters(db)
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(180))()
    with Session(db) as s:
        for team in ("21", "3"):
            roster = espn.parse_roster(load(FIXTURES / "espn" / f"nfl_roster_{team}.json"))
            upsert_roster(s, team, roster, KICKOFF - timedelta(minutes=50))
        s.commit()
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(46),
               before=remap_prices)()
    with Session(db) as s:
        t180 = s.scalars(select(OddsSnapshot).where(OddsSnapshot.slot == "t180")).one()
        linked = s.scalar(select(Price.espn_athlete_id).where(
            Price.snapshot_id == t180.id, Price.description == "Saquon Barkley").limit(1))
    assert linked == BARKLEY


def test_remap_recent_reaches_only_the_teams_asked_for(db, game):
    _forget_rosters(db)
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(46))()
    with Session(db) as s:
        for team in ("21", "3"):
            roster = espn.parse_roster(load(FIXTURES / "espn" / f"nfl_roster_{team}.json"))
            upsert_roster(s, team, roster, KICKOFF - timedelta(minutes=50))
        s.commit()
    assert remap_recent(db, KICKOFF, teams=["99"]) == 0
    assert remap_recent(db, KICKOFF) > 0
    assert _status(db, "Saquon Barkley") == "auto"


def test_the_worker_syncs_games_then_rosters_then_names_at_startup(db, game):
    _forget_rosters(db)
    CaptureT45(db, FakeOdds(), DEFAULT_BOOK_CHAIN, reserve=0, clock=at(46))()
    order = []

    def rosters():
        order.append("rosters")
        with Session(db) as s:
            for team in ("21", "3"):
                roster = espn.parse_roster(load(FIXTURES / "espn" / f"nfl_roster_{team}.json"))
                upsert_roster(s, team, roster, KICKOFF)
            s.commit()

    at_startup(db, lambda: order.append("schedule"), rosters, clock=at(40))()
    assert order == ["schedule", "rosters"]
    assert _status(db, "Saquon Barkley") == "auto"  # and then the names, against those rosters
