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
from juju.core.enums import DataSource, EventStatus, PlayKind
from juju.core.models import Game, HotGame, LiveStat, Play
from juju.dev_seed import seed_phi_chi_scenario
from juju.ingest import espn
from juju.ingest.http import RateLimiter
from juju.ingest.odds_api import ApiGameScore
from juju.ingest.router import Breakers, EspnRouter, Routed, Summary
from juju.worker.live import LiveSummary, PollLive, play_label
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


@respx.mock
def test_the_odds_api_scores_are_read_a_quarter_as_often_and_never_below_the_reserve(setup):
    """The owner's cut on 2026-10-15: every 4 minutes (was every minute) while ESPN works."""
    db, game_id, clock, poll, odds = setup
    for host in (WEB, SITE):  # a clock that doesn't move for 5 minutes makes it ask the other
        respx.get(f"{host}/scoreboard").mock(return_value=httpx.Response(200, json=board(27, 7)))
        respx.get(f"{host}/summary").mock(return_value=httpx.Response(200, json=summary()))
    calls = []
    odds.scores = lambda days_from=None: calls.append(clock.now) or []  # noqa: E731
    for _ in range(int(timedelta(minutes=8) / timedelta(seconds=5))):
        poll()
        clock.now += timedelta(seconds=5)
    assert len(calls) == 2 and calls[1] - calls[0] == timedelta(minutes=4)
    reserved = PollLive(db, poll._router, odds, clock=clock, reserve=1000)
    odds.quota_remaining = 1000
    reserved()
    assert len(calls) == 2  # a credit spent on scores would dip into the reserve


# --- Notable plays: interceptions, late rosters, labels, the CDN (2026-10-15) ------------------

HURTS_ID, EDWARDS = "4040715", "3121544"  # T.J. Edwards, CHI: "T.Edwards" in ESPN's text


def _interceptor(session, text_part: str) -> Play:
    return session.scalars(select(Play).where(Play.kind == "def_interception",
                                              Play.text.contains(text_part))).one()


@respx.mock
def test_an_interception_is_the_defenders_play_with_the_thrower_kept(setup):
    db, game_id, clock, poll, _ = setup
    respx.get(f"{WEB}/scoreboard").mock(return_value=httpx.Response(200, json=board(27, 7)))
    respx.get(f"{WEB}/summary").mock(return_value=httpx.Response(200, json=summary()))
    poll()
    with Session(db) as s:
        pick = _interceptor(s, "INTERCEPTED by T.Edwards")
        game = s.get(Game, game_id)
        assert pick.passer_espn_id == HURTS_ID
        assert pick.team_espn_id == game.home_espn_id  # CHI, the defense
        assert pick.espn_athlete_id == EDWARDS and pick.label == "T. Edwards interception"


@respx.mock
def test_a_play_stored_before_the_rosters_gets_its_player_later(setup):
    db, game_id, clock, poll, _ = setup
    from juju.core.models import Player
    from juju.worker.schedule import upsert_roster
    with Session(db) as s:
        saved = {p.espn_athlete_id: p for p in s.scalars(select(Player))}
        s.execute(Player.__table__.delete())
        s.commit()
    respx.get(f"{WEB}/scoreboard").mock(return_value=httpx.Response(200, json=board(27, 7)))
    respx.get(f"{WEB}/summary").mock(return_value=httpx.Response(200, json=summary()))
    poll()
    with Session(db) as s:
        assert s.scalars(select(Play).where(Play.espn_athlete_id.is_not(None))).all() == []
        for team in ("21", "3"):
            upsert_roster(s, team, espn.parse_roster(
                load(FIXTURES / "espn" / f"nfl_roster_{team}.json")), clock.now)
        s.commit()
    clock.now += timedelta(seconds=61)
    poll()
    with Session(db) as s:
        hurts_td = s.scalars(select(Play).where(Play.kind == "touchdown", Play.period == 2)).one()
        assert hurts_td.espn_athlete_id == HURTS_ID and hurts_td.label == "J. Hurts 1-yd TD run"
    assert len(saved) > 0


def test_return_touchdowns_are_labelled_by_their_type():
    def raw(type_text, passer=None, text="x"):
        return espn.RawPlay("1", 1, 2, "3:00", None, PlayKind.TOUCHDOWN, 45, text, "3",
                            "Kyle Dugger", passer, True, type_text)
    assert play_label("Kyle Dugger", raw("Interception Return Touchdown")) == \
        "K. Dugger 45-yd pick-six"
    assert play_label("Kyle Dugger", raw("Punt Return Touchdown")).endswith("punt return TD")
    assert play_label("Kyle Dugger", raw("Kickoff Return Touchdown")).endswith("kick return TD")
    assert play_label("Kyle Dugger", raw("Passing Touchdown", passer="J.Hurts")).endswith(
        "TD catch")
    assert play_label("Kyle Dugger", raw("Rushing Touchdown")).endswith("TD run")
    # Before the type was kept: a return TD whose text says "left" was a "TD run".
    assert play_label("Kyle Dugger", raw("Fumble Return Touchdown",
                                         text="returned left")).endswith("fumble return TD")


def test_a_score_change_read_from_the_cdn_is_read_once_more(setup):
    db, game_id, clock, poll, _ = setup
    box = espn.parse_box_score(summary())
    reads = []

    def cdn_summary(event_id, max_wait=0.0, *, only=None):
        reads.append(event_id)
        return Routed(DataSource.ESPN_CDN, Summary(box, []))
    poll._router.summary = cdn_summary
    poll._score_changed.add(game_id)
    with Session(db, expire_on_commit=False) as s:
        game = s.get(Game, game_id)
        assert poll._box(s, game, clock.now, LiveSummary())
        assert game_id in poll._score_changed  # no plays came: once more
        assert poll._box(s, game, clock.now, LiveSummary())
        assert game_id not in poll._score_changed  # but not forever
