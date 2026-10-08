"""The play that decided it, on the recorded PHI @ CHI game: live from ESPN's box score ("on or
around"), then exact from nflverse's play-by-play the next day."""
import copy
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal as D

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.api.views import FIRST_TD_UNCONFIRMED, check_notes, player_check, player_view
from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.card import Outcome
from juju.core.enums import EventStatus, FailureKind, Stat
from juju.core.markets import BY_KEY
from juju.core.models import DecidingPlay, Game, LiveStat, OddsSnapshot, Play, Price
from juju.dev_seed import RECORDED_KICKOFF, seed_phi_chi_scenario
from juju.ingest.http import FetchError, RateLimiter
from juju.ingest.nflverse import NflverseData
from juju.ingest.router import Breakers, EspnRouter
from juju.worker import deciding
from juju.worker.live import PollLive
from juju.worker.verify import VerifyGames, VerifySummary
from tests.support import BARKLEY, FIXTURES, KICKOFF, SMITH, load, nflverse_loader

pytestmark = pytest.mark.db

WEB = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl"
BOARD = load(FIXTURES / "espn" / "nfl_scoreboard_2026-09-28_final.json")
SUMMARY = load(FIXTURES / "espn" / "nfl_summary_401872963_full.json")
SMITH_30_YD_CATCH = "4018729631932"  # Q2 1:12
BURDEN = "4685278"  # scored the game's first touchdown
HURTS = "4040715"  # scored its second
IN_PLAY = {"clock": 312.0, "displayClock": "5:12", "period": 4,
           "type": {"id": "2", "name": "STATUS_IN_PROGRESS", "state": "in", "completed": False,
                    "description": "In Progress", "detail": "5:12 - 4th",
                    "shortDetail": "5:12 - 4th"}}
FINAL_AT = RECORDED_KICKOFF + timedelta(hours=3, minutes=10)
NEXT_MORNING = datetime.fromisoformat("2026-09-29T14:07:00+00:00")


def in_play(doc):
    doc = copy.deepcopy(doc)
    if "events" in doc:
        ev = doc["events"][0]
        ev["status"] = ev["competitions"][0]["status"] = IN_PLAY
    else:
        doc["header"]["competitions"][0]["status"] = IN_PLAY
    return doc


def smith_behind(summary):
    """The summary as it stood before Smith's 30-yard catch: 5 catches for 35, and the play
    not yet listed."""
    s = copy.deepcopy(summary)
    for team in s["boxscore"]["players"]:
        for group in team["statistics"]:
            if group["name"] != "receiving":
                continue
            for a in group["athletes"]:
                if a["athlete"]["id"] == SMITH:
                    a["stats"][group["keys"].index("receptions")] = "5"
                    a["stats"][group["keys"].index("receivingYards")] = "35"
    for drive in s["drives"]["previous"]:
        drive["plays"] = [p for p in drive["plays"] if p["id"] != SMITH_30_YD_CATCH]
    return s


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def live(db):
    clock = Clock(KICKOFF + timedelta(hours=2, minutes=30))
    with Session(db, expire_on_commit=False) as s:
        game = seed_phi_chi_scenario(s, "final", clock.now)
        s.execute(Game.__table__.update().values(
            status=EventStatus.IN_PROGRESS, final_at=None, last_polled_at=None,
            last_box_at=None, last_progress_at=None, period=None, clock_seconds=None))
        s.execute(LiveStat.__table__.delete())
        s.execute(Play.__table__.delete())
        s.commit()
    router = EspnRouter(Breakers(engine=None), RateLimiter(per_host_interval=0, per_minute=1000))
    return db, game.id, clock, PollLive(db, router, None, clock=clock)


def rows(db, athlete, stat, exact=None) -> dict[D, DecidingPlay]:
    with Session(db) as s:
        q = select(DecidingPlay).where(DecidingPlay.espn_athlete_id == athlete,
                                       DecidingPlay.stat == stat)
        if exact is not None:
            q = q.where(DecidingPlay.exact.is_(exact))
        return {r.line: r for r in s.scalars(q)}


def card(db, game_id, athlete, key, now, threshold=None):
    from juju.api.views import Focus
    focus = Focus(threshold=threshold) if threshold is not None else Focus()
    with Session(db) as s:
        view = player_view(s, s.get(Game, game_id), athlete, now, DEFAULT_BOOK_CHAIN, focus)
    return next(c for c in view.cards if c.key == key)


@respx.mock
def test_live_the_play_in_the_read_that_crossed_the_line(live):
    db, game_id, clock, poll = live
    respx.get(f"{WEB}/scoreboard").mock(return_value=httpx.Response(200, json=in_play(BOARD)))
    summary = respx.get(f"{WEB}/summary")
    summary.mock(return_value=httpx.Response(200, json=in_play(smith_behind(SUMMARY))))
    poll()
    # The game's first read: lines already passed get the game clock, never a guessed play.
    first = rows(db, SMITH, Stat.RECEPTIONS)
    assert set(first) >= {D("2.5"), D("4.5")} and D("5.5") not in first
    assert first[D("4.5")].text is None and (first[D("4.5")].period,
                                             first[D("4.5")].clock) == (4, "5:12")

    clock.now += timedelta(seconds=60)
    summary.mock(return_value=httpx.Response(200, json=in_play(SUMMARY)))
    poll()
    sixth = rows(db, SMITH, Stat.RECEPTIONS)[D("5.5")]
    assert (sixth.exact, sixth.text, sixth.period, sixth.clock) == (
        False, "D. Smith 30-yd catch", 2, "1:12")
    assert rows(db, SMITH, Stat.RECEIVING_YARDS)[D("59.5")].text == "D. Smith 30-yd catch"
    view = card(db, game_id, SMITH, "player_receptions", clock.now)
    assert view.outcome is Outcome.LOCKED
    assert view.decided_by == {"text": "D. Smith 30-yd catch", "period": 2, "clock": "1:12",
                               "exact": False}
    # A line he hasn't reached has no deciding play.
    assert card(db, game_id, SMITH, "player_reception_yds", clock.now).decided_by is None


def seeded(db) -> int:
    with Session(db, expire_on_commit=False) as s:
        return seed_phi_chi_scenario(s, "final", FINAL_AT + timedelta(minutes=30)).id


def test_next_day_the_exact_play_from_the_play_by_play(db):
    game_id = seeded(db)
    out = VerifyGames(db, lambda: NflverseData(Breakers(engine=None), nflverse_loader()))(
        NEXT_MORNING)
    assert out.verified == 1 and out.plays > 0
    sixth = rows(db, SMITH, Stat.RECEPTIONS, exact=True)[D("5.5")]
    assert (sixth.period, sixth.clock) == (3, "1:11")
    assert sixth.text.startswith("(Shotgun) 1-J.Hurts pass short right to 6-D.Smith")
    rec = card(db, game_id, SMITH, "player_receptions", NEXT_MORNING)
    assert rec.outcome is Outcome.WON and rec.decided_by["exact"] is True
    rush = card(db, game_id, BARKLEY, "player_rush_yds", NEXT_MORNING)
    assert rush.outcome is Outcome.WON and rush.line == D("73.5")
    assert rush.decided_by["clock"] == "15:00" and rush.decided_by["period"] == 3
    # Lost bets have no deciding play: nothing took them past the line.
    lost = card(db, game_id, SMITH, "player_reception_yds", NEXT_MORNING)
    assert lost.outcome is Outcome.LOST and lost.decided_by is None
    # Alternate lines too, when someone names the threshold.
    alt = card(db, game_id, SMITH, "player_reception_yds_alternate", NEXT_MORNING, D("59.5"))
    assert alt.outcome is Outcome.WON and alt.decided_by["clock"] == "0:19"


def test_the_exact_play_wins_over_the_live_one(db):
    game_id = seeded(db)
    with Session(db) as s:
        game = s.get(Game, game_id)
        deciding._save(s, game, SMITH, Stat.RECEPTIONS, D("5.5"), False, NEXT_MORNING,
                       text="D. Smith 30-yd catch", period=2, clock="1:12")
        s.commit()
    assert card(db, game_id, SMITH, "player_receptions", NEXT_MORNING).decided_by["exact"] \
        is False
    VerifyGames(db, lambda: NflverseData(Breakers(engine=None), nflverse_loader()))(NEXT_MORNING)
    assert card(db, game_id, SMITH, "player_receptions", NEXT_MORNING).decided_by["clock"] \
        == "1:11"


def test_no_exact_play_unless_the_play_by_play_adds_up(db):
    game_id = seeded(db)
    data = NflverseData(Breakers(engine=None), nflverse_loader())
    game_nfl = data.game_id(2026, "401872963")
    plays = data.plays(2026, frozenset({game_nfl}))[game_nfl]
    missing_a_catch = [p for p in plays if not (
        p["receiver_player_id"] == data.gsis_id(SMITH) and p["qtr"] == "3"
        and p["time"] == "01:11")]
    with Session(db) as s:
        game = s.get(Game, game_id)
        deciding.exact_plays(s, game, missing_a_catch, data.gsis_id, NEXT_MORNING)
        s.commit()
    assert D("5.5") not in rows(db, SMITH, Stat.RECEPTIONS, exact=True)  # 5 catches, not 6
    assert D("73.5") in rows(db, BARKLEY, Stat.RUSHING_YARDS, exact=True)


def test_without_the_play_by_play_the_game_still_verifies(db):
    seeded(db)
    fixture = nflverse_loader()

    def loader(dataset, season):
        if dataset == "pbp":
            raise FetchError("https://github.com/x", FailureKind.TRANSIENT, "timeout")
        return fixture(dataset, season)
    out = VerifyGames(db, lambda: NflverseData(Breakers(engine=None), loader))(NEXT_MORNING)
    assert (out.verified, out.plays) == (1, 0)
    assert rows(db, SMITH, Stat.RECEPTIONS, exact=True) == {}


def add_first_td_prices(db, game_id):
    """The recorded odds have no first-TD market, so two prices are added to the recorded
    snapshot, for these tests only."""
    with Session(db) as s:
        snapshot = s.scalars(select(OddsSnapshot).where(OddsSnapshot.game_id == game_id)).first()
        for athlete, name, american in ((BURDEN, "Luther Burden III", 650),
                                        (SMITH, "DeVonta Smith", 900)):
            s.add(Price(snapshot_id=snapshot.id, game_id=game_id, book="draftkings",
                        market_key="player_1st_td", outcome_name="Yes", description=name,
                        espn_athlete_id=athlete, point=None, american=american))
        s.commit()


def verify(db, loader=None):
    return VerifyGames(db, lambda: NflverseData(Breakers(engine=None),
                                                loader or nflverse_loader()))(NEXT_MORNING)


def pbp_with(change) -> bytes:
    """The recorded play-by-play, each play passed through `change(row) -> row`."""
    path = FIXTURES / "nflverse" / "play_by_play_2026.csv"
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(change(row) for row in rows)
    return out.getvalue().encode()


def test_first_touchdown_cards_show_the_touchdown(db):
    game_id = seeded(db)
    add_first_td_prices(db, game_id)
    won = card(db, game_id, BURDEN, "player_1st_td", NEXT_MORNING)
    assert won.outcome is Outcome.WON
    assert won.decided_by == {"text": "L. Burden III 8-yd TD catch", "period": 1,
                              "clock": "8:47", "exact": True}
    lost = card(db, game_id, SMITH, "player_1st_td", NEXT_MORNING)
    assert lost.outcome is Outcome.LOST and lost.decided_by == won.decided_by


def test_the_first_touchdown_is_verified_against_the_play_by_play(db):
    game_id = seeded(db)
    add_first_td_prices(db, game_id)
    won = card(db, game_id, BURDEN, "player_1st_td", NEXT_MORNING)
    assert won.verified is False
    assert won.notes == ["Awaiting verification against the official stats."]
    verify(db)
    with Session(db) as s:
        game = s.get(Game, game_id)
        assert (game.first_td_official, game.plays_checked_at) == (BURDEN, NEXT_MORNING)
    for athlete, outcome in ((BURDEN, Outcome.WON), (SMITH, Outcome.LOST)):
        c = card(db, game_id, athlete, "player_1st_td", NEXT_MORNING)
        assert c.outcome is outcome and c.verified is True
        assert c.notes == ["Verified against the official stats (nflverse)."]


def test_a_different_first_touchdown_in_the_play_by_play_is_never_applied(db):
    """The play-by-play credits Burden's touchdown to Hurts. ESPN's scorer still decides the
    cards; they stay as they were, unverified."""
    game_id = seeded(db)
    add_first_td_prices(db, game_id)
    hurts_gsis = "00-0036389"

    def to_hurts(row):
        if row["play_id"] == "315":  # Q1 8:56, Keenum to Burden, the first touchdown
            row["td_player_id"] = hurts_gsis
        return row
    verify(db, nflverse_loader(replace={"pbp": pbp_with(to_hurts)}))
    with Session(db) as s:
        assert s.get(Game, game_id).first_td_official == HURTS
    won = card(db, game_id, BURDEN, "player_1st_td", NEXT_MORNING)
    assert won.outcome is Outcome.WON and won.verified is False
    assert won.notes == [FIRST_TD_UNCONFIRMED]


def test_the_first_touchdown_awaits_the_play_by_play(db):
    """The game verifies without it, but its first touchdown hasn't been checked: the card
    says it is awaiting verification, not that the official stats disagree."""
    game_id = seeded(db)
    add_first_td_prices(db, game_id)
    fixture = nflverse_loader()

    def loader(dataset, season):
        if dataset == "pbp":
            raise FetchError("https://github.com/x", FailureKind.TRANSIENT, "timeout")
        return fixture(dataset, season)
    assert verify(db, loader).verified == 1
    with Session(db) as s:
        assert s.get(Game, game_id).plays_checked_at is None
    won = card(db, game_id, BURDEN, "player_1st_td", NEXT_MORNING)
    assert won.verified is False
    assert won.notes == ["Awaiting verification against the official stats."]


# --- A play-by-play published late ---------------------------------------------------------------

LATER = NEXT_MORNING + timedelta(hours=6)  # the 16:07 ET run


def pbp_before_the_game() -> bytes:
    """The play-by-play file as nflverse publishes it before this game is in it."""
    path = FIXTURES / "nflverse" / "play_by_play_2026.csv"
    return (path.read_text().splitlines()[0] + "\n").encode()


def run_at(db, now, calls=None, replace=None, loader=None) -> VerifySummary:
    return VerifyGames(db, lambda: NflverseData(
        Breakers(engine=None), loader or nflverse_loader(replace=replace, calls=calls)))(now)


def longest_catch_notes(db, game_id, now):
    with Session(db) as s:
        game = s.get(Game, game_id)
        rows = {r.stat: r for r in s.scalars(select(LiveStat).where(
            LiveStat.game_id == game_id, LiveStat.espn_athlete_id == SMITH))}
        return check_notes(player_check(BY_KEY["player_reception_longest"], game, rows, [],
                                        first_td=None), Outcome.WON)


def test_a_play_by_play_published_late_is_read_on_a_later_run(db):
    game_id = seeded(db)
    add_first_td_prices(db, game_id)
    out = verify(db, nflverse_loader(replace={"pbp": pbp_before_the_game()}))
    assert (out.verified, out.plays, out.late) == (1, 0, 0)
    with Session(db) as s:
        game = s.get(Game, game_id)
        assert game.verified_at == NEXT_MORNING and game.plays_checked_at is None
    # Until it is read, the cards it checks await verification; nothing says it disagrees.
    awaiting = ["Awaiting verification against the official stats."]
    assert card(db, game_id, BURDEN, "player_1st_td", NEXT_MORNING).notes == awaiting
    assert longest_catch_notes(db, game_id, NEXT_MORNING) == (False, awaiting)
    assert rows(db, SMITH, Stat.RECEPTIONS, exact=True) == {}

    # The next run finds it. The stats aren't checked again, so they aren't downloaded again.
    calls = []
    out = run_at(db, LATER, calls)
    assert (out.verified, out.late) == (0, 1) and out.plays > 0
    assert "player_stats" not in calls and "pbp" in calls
    with Session(db) as s:
        game = s.get(Game, game_id)
        assert (game.verified_at, game.plays_checked_at) == (NEXT_MORNING, LATER)
        assert game.first_td_official == BURDEN
    verified = ["Verified against the official stats (nflverse)."]
    assert card(db, game_id, BURDEN, "player_1st_td", LATER).notes == verified
    assert longest_catch_notes(db, game_id, LATER) == (True, verified)
    assert rows(db, SMITH, Stat.RECEPTIONS, exact=True)[D("5.5")].clock == "1:11"

    # Read once: the run after downloads nothing.
    calls = []
    assert run_at(db, LATER + timedelta(hours=18), calls) == VerifySummary() and calls == []


def test_a_play_by_play_still_missing_after_a_week_is_left_alone(db):
    seeded(db)
    verify(db, nflverse_loader(replace={"pbp": pbp_before_the_game()}))
    calls = []
    still_missing = {"pbp": pbp_before_the_game()}
    assert run_at(db, LATER, calls, still_missing).late == 0 and "pbp" in calls  # tried
    calls = []
    a_week_on = RECORDED_KICKOFF + timedelta(days=7, minutes=1)
    assert run_at(db, a_week_on, calls) == VerifySummary() and calls == []


def test_a_retry_while_nflverse_is_down_waits_for_the_next_run(db):
    game_id = seeded(db)
    verify(db, nflverse_loader(replace={"pbp": pbp_before_the_game()}))

    def down(dataset, season):
        raise FetchError("https://github.com/x", FailureKind.TRANSIENT, "timeout")
    assert run_at(db, LATER, loader=down) == VerifySummary()  # logged, not raised
    with Session(db) as s:
        assert s.get(Game, game_id).plays_checked_at is None
    assert run_at(db, LATER + timedelta(hours=1)).late == 1
