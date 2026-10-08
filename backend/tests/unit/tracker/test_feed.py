"""The tracker's view of ESPN and The Odds API over Juju's router and client (tracker/feed.py)."""
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest
import respx

from juju.core.enums import DataSource, Sport
from juju.ingest import espn, guards
from juju.ingest.http import RateLimiter
from juju.ingest.odds_api import OddsApiClient
from juju.ingest.router import Breakers, EspnRouter, Routed
from juju.tracker import feed
from juju.tracker.models import MarketType as M

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def load(kind: str, name: str):
    return json.loads((FIXTURES / kind / name).read_text())


# --- Plausibility (ported from parlaytracker@c3bd43c tests/unit/test_guards.py) --------------


@pytest.mark.parametrize(("market", "ok", "bad"), [
    (M.PLAYER_RECEIVING_YARDS, (-30, 400), (-31, 401)),
    (M.PLAYER_RUSHING_YARDS, (-30, 400), (-31, 401)),
    (M.PLAYER_PASSING_YARDS, (-30, 700), (-31, 701)),
    (M.PLAYER_RECEPTIONS, (0, 25), (-1, 26)),
])
def test_stat_bounds(market, ok, bad):
    for v in ok:
        feed.check_market_stats({market: {"1": D(v)}})
    for v in bad:
        with pytest.raises(guards.ImplausibleError):
            feed.check_market_stats({market: {"1": D(v)}})


def test_points_have_no_bounds_in_the_spec():
    feed.check_market_stats({M.PLAYER_POINTS: {"1": D(200)}})


# --- One definition of the NFL columns --------------------------------------------------------


def test_the_nfl_markets_read_exactly_the_columns_of_jujus_matching_stats():
    for market, stat in feed.NFL_STATS.items():
        assert feed.MARKET_COLUMNS[Sport.NFL][market] is espn.STAT_COLUMNS[stat]
    assert set(feed.MARKET_COLUMNS[Sport.NFL]) == {m for m in M if m.value.startswith("player_")
                                                 and m is not M.PLAYER_POINTS}


def test_an_nba_box_score_gives_points_only_with_the_tracker_columns():
    payload = load("espn", "nba_summary_401810723_final.json")
    assert not any(espn.parse_box_score(payload).stats.values())  # Juju's NFL columns: nothing
    box = feed.parse_box_score(Sport.NBA, payload)
    assert set(box.stats) == {M.PLAYER_POINTS} and box.stats[M.PLAYER_POINTS]


# --- EspnFeed: parlaytracker's calls, through Juju's router ----------------------------------


class Recorder:
    """Stands in for EspnRouter and records how it was called."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.breakers = Breakers(engine=None)

    def scoreboard(self, day, max_wait=0.0, **kw):
        self.calls.append(("scoreboard", day, max_wait, kw))
        return Routed(DataSource.ESPN_WEB, espn.ScoreboardResult([], {}))

    def box_score(self, espn_event_id, max_wait=0.0, **kw):
        self.calls.append(("box_score", espn_event_id, max_wait, kw))
        return Routed(DataSource.ESPN_SITE, None)


def test_the_feed_asks_the_router_for_the_sport_and_the_tracker_columns():
    router = Recorder()
    espn_feed = feed.EspnFeed(router)
    assert espn_feed.breakers is router.breakers
    espn_feed.scoreboard(Sport.NBA, date(2026, 3, 1), 5.0, only=DataSource.ESPN_SITE)
    espn_feed.box_score(Sport.NHL, "401803298", only=DataSource.ESPN_CDN)
    (_, day, wait, kw), (_, event, _, box_kw) = router.calls
    assert (day, wait, kw) == (date(2026, 3, 1), 5.0,
                               {"only": DataSource.ESPN_SITE, "sport": Sport.NBA})
    assert event == "401803298" and box_kw["sport"] is Sport.NHL
    assert box_kw["columns"] is feed.MARKET_COLUMNS[Sport.NHL]
    assert box_kw["check_stats"] is feed.check_market_stats
    assert box_kw["only"] is DataSource.ESPN_CDN


@respx.mock
def test_an_implausible_market_value_rejects_the_box_score_through_the_real_router():
    doc = load("espn", "nfl_summary_401872958_final.json")
    for team in doc["boxscore"]["players"]:
        for group in team["statistics"]:
            if group["name"] == "passing":
                for a in group["athletes"]:
                    a["stats"][group["keys"].index("completions/passingAttempts")] = "71/80"
    respx.get(host="site.web.api.espn.com").mock(return_value=httpx.Response(200, json=doc))
    router = EspnRouter(Breakers(engine=None), RateLimiter(per_host_interval=0, per_minute=1000))
    with pytest.raises(Exception) as info:
        feed.EspnFeed(router).box_score(Sport.NFL, "401872958", only=DataSource.ESPN_WEB)
    assert "player_pass_completions 71" in str(info.value)


# --- OddsFeed: one client, one quota, Juju's books -------------------------------------------


@respx.mock
def test_odds_for_another_sport_use_its_key_and_jujus_books():
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/events/abc/odds"
    route = respx.get(url).mock(return_value=httpx.Response(
        200, headers={"x-requests-remaining": "480", "x-requests-last": "1"},
        json={"id": "abc", "sport_key": "basketball_nba", "commence_time": "2026-03-01T20:00:00Z",
              "home_team": "New York Knicks", "away_team": "Boston Celtics", "bookmakers": []}))
    client = OddsApiClient("k", Breakers(engine=None),
                           RateLimiter(per_host_interval=0, per_minute=1000))
    odds = feed.OddsFeed(client, ["hardrockbet", "draftkings"])
    result = odds.event_odds(Sport.NBA, "abc", ["player_points"])
    assert result.home_team == "New York Knicks" and odds.quota_remaining == 480
    params = route.calls.last.request.url.params
    assert params["bookmakers"] == "hardrockbet,draftkings" and "regions" not in params
    assert params["markets"] == "player_points"


@respx.mock
def test_jujus_own_calls_still_ask_for_the_nfl():
    route = respx.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events").mock(
        return_value=httpx.Response(200, json=[]))
    client = OddsApiClient("k", Breakers(engine=None),
                           RateLimiter(per_host_interval=0, per_minute=1000))
    assert client.events() == [] and route.called
