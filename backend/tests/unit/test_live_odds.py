"""The live odds service (ingest/live_odds.py): the cache, the credits, and what a reply may
reveal. The Odds API is a fake built from the recorded PIT @ CLE reply; no test uses the
network."""
import threading
import time

import httpx
import pytest
import respx

from juju.core.enums import FailureKind
from juju.ingest.http import FetchError, RateLimited, RateLimiter
from juju.ingest.live_odds import (
    MAX_ODDS_TTL, LiveError, LiveOdds, market_offered, postings_for, subjects,
)
from juju.ingest.odds_api import OddsApiClient, RequestRejected
from juju.ingest.router import Breakers, ProviderOpen
from tests.live_support import CHAIN, EVENT_ID, ANOTHER_EVENT, Clock, FakeLive

METCALF = lambda s: s == "DK Metcalf"  # noqa: E731


def service(source=None, clock=None, **kw):
    source = source or FakeLive()
    clock = clock or Clock()
    return LiveOdds(source, CHAIN, clock=clock, initial_remaining=source.remaining, **kw), \
        source, clock


# --- The cache -------------------------------------------------------------------------------


def test_a_fresh_question_makes_one_call_and_a_repeat_makes_none():  # cache miss, then hit
    live, source, _ = service()
    first = live.odds(EVENT_ID, "player_receptions")
    again = live.odds(EVENT_ID, "player_receptions")
    assert not first.cached and first.cost == 1
    assert again.cached and again.cost == 0 and again.fetched_at == first.fetched_at
    assert source.calls == [("odds", EVENT_ID, ("player_receptions",))]


def test_the_cache_is_per_market_not_per_player():
    """The reply holds every player, so a second player in the same market is free. (Only the
    asked-for player's lines ever leave: `postings_for`.)"""
    live, source, _ = service()
    live.odds(EVENT_ID, "player_receptions")
    live.odds(EVENT_ID, "player_receptions")
    assert len(source.paid_calls) == 1
    live.odds(EVENT_ID, "player_reception_yds")          # another market: a call
    live.odds(ANOTHER_EVENT, "player_receptions")        # another game: a call
    assert len(source.paid_calls) == 3


def test_odds_are_never_served_after_60_seconds():  # cache miss on expiry
    live, source, clock = service()
    live.odds(EVENT_ID, "player_receptions")
    clock.advance(59)
    assert live.odds(EVENT_ID, "player_receptions").cached
    clock.advance(1)  # 60 s old: gone
    again = live.odds(EVENT_ID, "player_receptions")
    assert not again.cached and again.cost == 1
    assert len(source.paid_calls) == 2


def test_the_odds_time_can_be_shortened_never_lengthened():
    long, _, _ = service(odds_ttl=3600)
    assert long.odds_ttl.total_seconds() == MAX_ODDS_TTL == 60
    short, source, clock = service(odds_ttl=10)
    short.odds(EVENT_ID, "h2h")
    clock.advance(11)
    assert not short.odds(EVENT_ID, "h2h").cached
    assert len(source.paid_calls) == 2


def test_the_market_list_is_kept_five_minutes():
    live, source, clock = service()
    first = live.markets(EVENT_ID)
    clock.advance(299)
    assert live.markets(EVENT_ID).cached
    clock.advance(2)
    assert not live.markets(EVENT_ID).cached
    assert [c for c in source.calls if c[0] == "markets"] == [("markets", EVENT_ID)] * 2
    assert "player_receptions" in first.value and "player_rush_tds" not in first.value


def test_nothing_is_fetched_until_someone_asks():
    live, source, _ = service()
    assert source.calls == []
    assert live.remaining == 20_000 and live.spent == 0
    assert not live.cached(EVENT_ID, "player_receptions")
    assert source.calls == []  # asking whether it is cached doesn't fetch it


def test_two_questions_at_once_make_one_call():
    live, source, _ = service()
    real = source.event_odds

    def slow(*a, **k):
        time.sleep(0.15)
        return real(*a, **k)

    source.event_odds = slow  # type: ignore[method-assign]
    results = []

    def ask():
        results.append(live.odds(EVENT_ID, "player_receptions"))

    threads = [threading.Thread(target=ask) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(source.paid_calls) == 1
    assert sorted(r.cached for r in results) == [False] + [True] * 5
    assert live.spent == 1


# --- Credits ---------------------------------------------------------------------------------


def test_each_unique_call_takes_a_credit_and_a_cached_one_takes_none():
    seen = []
    live, source, _ = service(on_quota=lambda n, at: seen.append(n))
    assert live.remaining == 20_000
    live.odds(EVENT_ID, "player_receptions")
    assert live.remaining == 19_999 and live.spent == 1
    live.odds(EVENT_ID, "player_receptions")                # cached
    assert live.remaining == 19_999 and live.spent == 1
    live.odds(EVENT_ID, "player_reception_yds")
    live.markets(EVENT_ID)
    assert live.remaining == 19_997 and live.spent == 3
    assert seen == [19_999, 19_998, 19_997]                 # written only when a call was made


def test_the_credits_left_are_the_apis_count_not_ours():
    live, source, _ = service()
    live.odds(EVENT_ID, "h2h")
    source.remaining = 12_345                               # the account also spent elsewhere
    live.odds(EVENT_ID, "totals")                           # this call takes one more
    assert live.remaining == 12_344


def test_a_reply_that_doesnt_say_the_cost_is_charged_one_per_market():
    live, source, _ = service(FakeLive(hide_cost=True))
    assert live.odds(EVENT_ID, "player_receptions").cost == 1
    assert live.spent == 1


def test_a_failed_write_of_the_count_never_fails_the_question():
    def boom(n, at):
        raise RuntimeError("db down")

    live, _, _ = service(on_quota=boom)
    assert not live.odds(EVENT_ID, "h2h").cached


# --- Failures --------------------------------------------------------------------------------


@pytest.mark.parametrize("error, kind, text", [
    (RequestRejected("HTTP 422: unknown market", 422), "not_offered", "doesn't offer that market"),
    (RequestRejected("HTTP 401: OUT_OF_USAGE_CREDITS", 401), "out_of_credits", "used up"),
    (RequestRejected("HTTP 401: INVALID_KEY", 401), "unavailable", "didn't accept"),
    (FetchError("u", FailureKind.TRANSIENT, "timeout"), "unavailable", "didn't answer"),
    (ProviderOpen("odds_api", None), "unavailable", "paused"),
    (RateLimited("api.the-odds-api.com", 2.0), "unavailable", "Too many"),
])
def test_a_failure_says_so_plainly_and_is_not_cached(error, kind, text):
    live, source, _ = service(FakeLive(fail=error))
    with pytest.raises(LiveError) as e:
        live.odds(EVENT_ID, "player_receptions")
    assert e.value.kind == kind and text in e.value.message
    source.fail = None                                      # it comes back: the next one asks again
    assert not live.odds(EVENT_ID, "player_receptions").cached


# --- What a reply may reveal -----------------------------------------------------------------


def test_one_players_outcomes_come_out_of_a_reply_and_nobody_elses():
    live, _, _ = service()
    reply = live.odds(EVENT_ID, "player_receptions").value
    assert market_offered(reply, "player_receptions")
    names = subjects(reply, "player_receptions")
    assert "DK Metcalf" in names and "Harold Fannin Jr." in names
    mine = postings_for(reply, {"player_receptions"}, METCALF)
    assert mine and {p.name for p in mine} == {"Over", "Under"}
    assert {p.book for p in mine} >= {"hardrockbet", "draftkings", "fanduel"}
    # a different player's postings aren't among them: the prices of two players differ
    fannin = postings_for(reply, {"player_receptions"}, lambda s: s == "Harold Fannin Jr.")
    assert fannin and not set(map(id, mine)) & set(map(id, fannin))
    assert postings_for(reply, {"player_receptions"}, lambda s: s == "Nobody") == []


def test_a_market_nobody_posts_is_not_offered():
    live, _, _ = service()
    reply = live.odds(EVENT_ID, "player_rush_tds").value
    assert not market_offered(reply, "player_rush_tds")
    assert subjects(reply, "player_rush_tds") == []


def test_team_markets_name_the_team_and_a_game_total_names_nobody():
    live, _, _ = service()
    spreads = live.odds(EVENT_ID, "spreads").value
    assert set(subjects(spreads, "spreads")) == {"Cleveland Browns", "Pittsburgh Steelers"}
    browns = postings_for(spreads, {"spreads"}, lambda s: s == "Cleveland Browns")
    assert {p.point for p in browns} and all(p.name == "Cleveland Browns" for p in browns)
    totals = live.odds(EVENT_ID, "totals").value
    assert subjects(totals, "totals") == []
    assert {p.name for p in postings_for(totals, {"totals"}, lambda s: True)} == {"Over", "Under"}


# --- The client's new call -------------------------------------------------------------------


@respx.mock
def test_the_markets_call_reads_the_apis_list_and_its_cost():
    url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{EVENT_ID}/markets"
    route = respx.get(url).mock(return_value=httpx.Response(
        200, headers={"x-requests-remaining": "19999", "x-requests-last": "1"},
        json={"id": EVENT_ID, "bookmakers": [
            {"key": "draftkings", "markets": [{"key": "h2h", "last_update": "2026-10-01T22:00:00Z"},
                                              {"key": "player_receptions"}]},
            {"key": "fanduel", "markets": [{"key": "h2h"}]}]}))
    client = OddsApiClient("a-key", Breakers(engine=None),
                           RateLimiter(per_host_interval=0, per_minute=1000))
    reply = client.event_markets(EVENT_ID, ["draftkings", "fanduel"])
    assert {m.key for b in reply.bookmakers for m in b.markets} == {"h2h", "player_receptions"}
    assert client.last_cost == 1 and client.quota_remaining == 19_999
    request = route.calls.last.request
    assert request.url.params["bookmakers"] == "draftkings,fanduel"
    assert "regions" not in request.url.params
