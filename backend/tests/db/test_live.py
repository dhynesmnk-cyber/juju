"""The live loop on a fake clock, with ESPN mocked from the recorded PHI @ CHI game edited to a
moment in the fourth quarter (parlaytracker's approach until a real game is recorded)."""
import copy
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.api.views import player_view
from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.card import Outcome
from juju.core.enums import DataSource, EventStatus
from juju.core.models import Game, HotGame, LiveStat, Play
from juju.dev_seed import seed_phi_chi_scenario
from juju.ingest import espn
from juju.ingest.http import RateLimiter
from juju.ingest.odds_api import ApiGameScore
from juju.ingest.router import Breakers, EspnRouter
from juju.worker.live import PollLive
from tests.support import BARKLEY, FIXTURES, KICKOFF, FakeOdds, load

pytestmark = pytest.mark.db

WEB = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl"
SITE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CDN = "https://cdn.espn.com/core/nfl/game"
BOARD = load(FIXTURES / "espn" / "nfl_scoreboard_2026-09-28_final.json")
SUMMARY = load(FIXTURES / "espn" / "nfl_summary_401872963_full.json")
IN_PLAY_STATUS = {"clock": 312.0, "displayClock": "5:12", "period": 4,
                  "type": {"id": "2", "name": "STATUS_IN_PROGRESS", "state": "in",
                           "completed": False, "description": "In Progress",
                           "detail": "5:12 - 4th", "shortDetail": "5:12 - 4th"}}


def board(home: int, away: int, period=4, clock=312.0):
    b = copy.deepcopy(BOARD)
    ev = b["events"][0]
    ev["status"] = {**IN_PLAY_STATUS, "clock": clock, "period": period}
    ev["competitions"][0]["status"] = ev["status"]
    for c in ev["competitions"][0]["competitors"]:
        c["score"] = str(home if c["homeAway"] == "home" else away)
    return b


def summary():
    s = copy.deepcopy(SUMMARY)
    s["header"]["competitions"][0]["status"] = IN_PLAY_STATUS
    return s


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def setup(db):
    clock = Clock(KICKOFF + timedelta(hours=2, minutes=30))
    with Session(db, expire_on_commit=False) as s:
        game = seed_phi_chi_scenario(s, "final", clock.now)
        # Back to "in play, never polled": the loop has to find everything itself.
        s.execute(Game.__table__.update().values(
            status=EventStatus.IN_PROGRESS, final_at=None, last_polled_at=None,
            last_box_at=None, last_progress_at=None, home_score=None, away_score=None,
            period=None, clock_seconds=None))
        s.execute(LiveStat.__table__.delete())
        s.execute(Play.__table__.delete())
        s.commit()
    router = EspnRouter(Breakers(engine=None), RateLimiter(per_host_interval=0, per_minute=1000))
    odds = FakeOdds()
    return db, game.id, clock, PollLive(db, router, odds, clock=clock), odds


def game_row(db, game_id) -> Game:
    with Session(db) as s:
        return s.get(Game, game_id)


@respx.mock
def test_first_tick_reads_state_box_score_and_plays(setup):
    db, game_id, clock, poll, _ = setup
    respx.get(f"{WEB}/scoreboard").mock(return_value=httpx.Response(200, json=board(20, 7)))
    respx.get(f"{WEB}/summary").mock(return_value=httpx.Response(200, json=summary()))
    out = poll()
    assert (out.scoreboards, out.boxes) == (1, 1)
    g = game_row(db, game_id)
    assert (g.status, g.home_score, g.away_score, g.period) == (EventStatus.IN_PROGRESS, 27, 7, 4)
    assert g.live_source is DataSource.ESPN_WEB and g.last_box_at == clock.now
    with Session(db) as s:
        stats = dict(s.execute(select(LiveStat.stat, LiveStat.value).where(
            LiveStat.espn_athlete_id == BARKLEY)).all())
        assert stats["rushing_yards"] == 82 and stats["receptions"] >= 0
        assert s.scalar(select(Play.label).where(Play.kind == "touchdown", Play.period == 2)) \
            == "J. Hurts 1-yd TD run"


@respx.mock
def test_cadence_hot_games_and_score_changes(setup):
    db, game_id, clock, poll, _ = setup
    sb = respx.get(f"{WEB}/scoreboard").mock(return_value=httpx.Response(200, json=board(27, 7)))
    box = respx.get(f"{WEB}/summary").mock(return_value=httpx.Response(200, json=summary()))
    poll()
    clock.now += timedelta(seconds=10)
    poll()
    assert (sb.call_count, box.call_count) == (1, 1)  # nothing due yet
    with Session(db) as s:
        s.add(HotGame(game_id=game_id, last_lookup_at=clock.now))
        s.commit()
    clock.now += timedelta(seconds=10)  # 20 s since the last box score
    poll()
    assert (sb.call_count, box.call_count) == (2, 2)  # hot: every 20 s
    sb.mock(return_value=httpx.Response(200, json=board(27, 14, clock=250.0)))
    clock.now += timedelta(seconds=15)
    poll()
    assert box.call_count == 3  # a score change: straight to the box score


@respx.mock
def test_a_stale_reading_is_discarded(setup):
    db, game_id, clock, poll, _ = setup
    respx.get(f"{WEB}/summary").mock(return_value=httpx.Response(200, json=summary()))
    sb = respx.get(f"{WEB}/scoreboard").mock(
        return_value=httpx.Response(200, json=board(27, 7, clock=300.0)))
    poll()
    sb.mock(return_value=httpx.Response(200, json=board(20, 7, period=3, clock=100.0)))
    clock.now += timedelta(seconds=15)
    poll()
    g = game_row(db, game_id)
    assert (g.period, g.home_score) == (4, 27)


@respx.mock
def test_when_espn_is_blocked_the_odds_api_keeps_the_score_and_cards_say_so(setup):
    db, game_id, clock, poll, odds = setup
    blocked = httpx.Response(403, text="Access Denied")
    for host in (WEB, SITE):
        respx.get(f"{host}/scoreboard").mock(return_value=blocked)
        respx.get(f"{host}/summary").mock(return_value=blocked)
    respx.get(CDN).mock(return_value=blocked)
    g = game_row(db, game_id)
    odds.scores = lambda days_from=None: [ApiGameScore(  # noqa: E731
        id=g.odds_event_id, commence_time=g.commence_time, completed=False,
        home_team="Chicago Bears", away_team="Philadelphia Eagles",
        scores=[{"name": "Chicago Bears", "score": "27"},
                {"name": "Philadelphia Eagles", "score": "14"}])]
    poll()
    g = game_row(db, game_id)
    assert (g.home_score, g.away_score, g.live_source) == (27, 14, DataSource.ODDS_API)
    # Nothing new for 6 minutes: the card shows the price, and says live status is unavailable.
    clock.now += timedelta(minutes=6)
    odds.scores = lambda days_from=None: []  # noqa: E731
    poll()
    with Session(db) as s:
        view = player_view(s, s.get(Game, game_id), BARKLEY, clock.now, DEFAULT_BOOK_CHAIN)
    rush = next(c for c in view.cards if c.key == "player_rush_yds")
    assert rush.outcome is Outcome.UNAVAILABLE and rush.price is not None


@respx.mock
def test_final_then_one_more_box_score_after_ten_minutes(setup):
    db, game_id, clock, poll, _ = setup
    final_board = copy.deepcopy(BOARD)
    respx.get(f"{WEB}/scoreboard").mock(return_value=httpx.Response(200, json=final_board))
    box = respx.get(f"{WEB}/summary").mock(return_value=httpx.Response(200, json=SUMMARY))
    poll()
    g = game_row(db, game_id)
    assert g.status is EventStatus.FINAL and g.final_at == clock.now
    calls = box.call_count
    clock.now += timedelta(minutes=11)
    poll()
    assert box.call_count == calls + 1  # the settling recheck
    clock.now += timedelta(minutes=5)
    poll()
    assert box.call_count == calls + 1


def test_espn_sport_constant_is_nfl():
    assert espn.Sport.NFL.value == "nfl"
