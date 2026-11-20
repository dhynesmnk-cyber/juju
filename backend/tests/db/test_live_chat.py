"""The live chat's endpoints (api/live_chat.py), end to end over a fake Odds API built from the
recorded PIT @ CLE reply. The numbers asserted here are read from that fixture, never from the
code under test.

Rules held here: one player's lines at a time; a 60-second cache that a repeat question never
bypasses; credits deducted for each unique call and never for a cached one; "Market not
available" said plainly; the archive never read.
"""
import ast
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from juju.api import app as app_module, live_chat
from juju.config import get_settings
from juju.core import odds
from juju.core.card import STAKE
from juju.core.enums import DataSource, Stat
from juju.core.models import Game, LiveStat, Player
from juju.ingest.live_odds import LiveOdds
from juju.ingest.odds_api import RequestRejected
from tests.live_support import (
    CHAIN, EVENT_ID, ODDS_FILE, START, Clock, FakeLive,
)

pytestmark = pytest.mark.db

RAW = json.loads(ODDS_FILE.read_text())


def fixture_price(book: str, market: str, subject: str | None, name: str, point=None) -> int:
    """A price, read straight from the recorded reply."""
    b = next(b for b in RAW["bookmakers"] if b["key"] == book)
    m = next(m for m in b["markets"] if m["key"] == market)
    return next(o["price"] for o in m["outcomes"]
                if o["name"] == name and o.get("description") == subject
                and (point is None or o.get("point") == point))


def returns(american: int) -> str:
    return format(odds.payout(STAKE, odds.decimal_odds(american)), "f")


@pytest.fixture
def source() -> FakeLive:
    return FakeLive()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(db, monkeypatch, source, clock):
    monkeypatch.setenv("DATABASE_URL", str(db.url.render_as_string(hide_password=False)))
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    get_settings.cache_clear()
    factory = sessionmaker(bind=db, expire_on_commit=False)

    def session():
        with factory() as s:
            yield s

    live = LiveOdds(source, CHAIN, clock=clock, initial_remaining=source.remaining)
    app_module.app.dependency_overrides[app_module.get_session] = session
    app_module.app.dependency_overrides[live_chat.live_odds] = lambda: live
    test_client = TestClient(app_module.app)
    test_client.live = live  # type: ignore[attr-defined]
    yield test_client
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def ask(client, text, game=EVENT_ID):
    r = client.post("/api/live/ask", json={"game_id": game, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


# --- The schedule and the market list ---------------------------------------------------------


def test_the_schedule_lists_games_with_no_prices_and_costs_nothing(client, source):
    r = client.get("/api/live/games")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert [g["id"] for g in body["games"]][0] == EVENT_ID
    assert body["games"][0]["label"] == "Pittsburgh Steelers @ Cleveland Browns"
    assert "price" not in json.dumps(body["games"]) and "american" not in json.dumps(body)
    assert body["credits"]["remaining"] == 20_000        # a free call
    assert source.paid_calls == []


def test_the_markets_a_game_offers_are_named_without_prices_and_cached(client, source):
    first = client.get("/api/live/markets", params={"game_id": EVENT_ID}).json()
    keys = [m["key"] for m in first["markets"]]
    assert "player_receptions" in keys and "spreads" in keys and "player_rush_tds" not in keys
    assert first["cost"] == 1 and first["cached"] is False
    assert first["credits"]["remaining"] == 19_999
    assert "american" not in json.dumps(first)
    again = client.get("/api/live/markets", params={"game_id": EVENT_ID}).json()
    assert again["cached"] is True and again["cost"] == 0
    assert again["credits"]["remaining"] == 19_999
    assert len(source.paid_calls) == 1


def test_a_game_that_is_not_on_the_schedule_costs_nothing(client, source):
    assert client.get("/api/live/markets",
                      params={"game_id": "0123456789abcdef"}).status_code == 404
    assert client.get("/api/live/markets", params={"game_id": "no way!"}).status_code == 422
    assert client.post("/api/live/ask", json={"game_id": "0123456789abcdef",
                                              "text": "Metcalf 4 catches"}).status_code == 404
    assert source.paid_calls == []


# --- The primitive: GET /api/live-markets -----------------------------------------------------


def lines(body):
    return {Decimal(x["point"]): x["sides"] for x in body["lines"]}


def test_live_markets_gives_one_players_lines_with_every_books_price(client):
    r = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "player_receptions", "player": "Metcalf"})
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "DK Metcalf" and body["cost"] == 1 and body["cached"] is False
    by_point = lines(body)
    assert list(by_point) == [Decimal("3.5")]
    over, under = by_point[Decimal("3.5")]["Over"], by_point[Decimal("3.5")]["Under"]
    books = [x["book"] for x in over]
    assert books[0] == "hardrockbet" and books == [b for b in CHAIN if b in books]  # chain order
    assert over[0]["american"] == fixture_price("hardrockbet", "player_receptions", "DK Metcalf",
                                                "Over") == -125
    assert under[0]["american"] == -105 and over[0]["returns"] == "18.00"
    assert {x["book_name"] for x in over} >= {"Hard Rock Bet", "DraftKings", "FanDuel"}
    assert body["disclaimer"].startswith("Live prices")


def test_live_markets_never_lists_other_players(client):
    body = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "player_receptions", "player": "Metcalf"}).json()
    text = json.dumps(body)
    for other in ("Fannin", "Concepcion", "Freiermuth", "Pittman", "Judkins", "Warren"):
        assert other not in text
    assert len(body["lines"]) == 1


def test_live_markets_needs_a_player_or_team_never_a_whole_market(client, source):
    for params in ({"game_id": EVENT_ID, "market": "player_receptions"},
                   {"game_id": EVENT_ID, "market": "player_receptions", "player": "  "}):
        r = client.get("/api/live-markets", params=params)
        assert r.status_code == 400 and "one at a time" in r.json()["detail"]
    assert client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "made_up", "player": "x"}).status_code == 422
    assert source.paid_calls == []                                # refused before any spend


def test_live_markets_says_market_not_available(client, source):
    source.drop = {"player_receptions"}                           # the books don't post it
    r = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "player_receptions", "player": "Metcalf"})
    assert r.status_code == 404 and r.json()["detail"].startswith("Market not available")
    source.drop = set()
    r = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "player_rush_tds", "player": "Warren"})
    assert r.status_code == 404 and "Market not available" in r.json()["detail"]


def test_live_markets_says_so_when_the_player_isnt_posted_and_offers_near_names(client):
    r = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "player_receptions", "player": "Klein"})
    assert r.status_code == 404
    assert r.json()["detail"].startswith("Market not available: no book is posting Receptions "
                                         "for \"Klein\"")


def test_live_markets_for_a_team_and_the_total(client):
    browns = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "spreads", "player": "Browns"}).json()
    assert browns["subject"] == "Cleveland Browns"
    hr = lines(browns)[Decimal("2.5")]["Cleveland Browns"][0]
    assert hr["book"] == "hardrockbet" and hr["american"] == -105
    total = client.get("/api/live-markets", params={
        "game_id": EVENT_ID, "market": "totals", "player": "game"}).json()
    assert total["subject"] == "Game total" and set(lines(total)) >= {Decimal("38.0")}


def test_live_markets_repeats_within_a_minute_are_free_and_a_later_one_is_not(
        client, source, clock):                                   # cache hit, then cache miss
    params = {"game_id": EVENT_ID, "market": "player_receptions", "player": "Metcalf"}
    first = client.get("/api/live-markets", params=params).json()
    second = client.get("/api/live-markets", params={**params, "player": "Fannin"}).json()
    assert first["cached"] is False and second["cached"] is True and second["cost"] == 0
    assert second["subject"] == "Harold Fannin Jr."               # another player: same call
    assert len(source.paid_calls) == 1
    clock.advance(61)
    third = client.get("/api/live-markets", params=params).json()
    assert third["cached"] is False and third["cost"] == 1
    assert len(source.paid_calls) == 2


# --- The chat: POST /api/live/ask -------------------------------------------------------------


def test_four_catches_means_over_3_5_and_the_answer_is_plain_language(client, source):
    body = ask(client, "Metcalf got 4 catches")
    assert body["kind"] == "answer"
    assert body["message"].startswith(
        "DK Metcalf Over 3.5 receptions pays -125 at Hard Rock Bet right now: "
        "$10 would return $18.00 if it hits.")
    assert "Other books: DraftKings -133" in body["message"]
    leg = body["legs"][0]
    assert leg["exact"] and leg["picks"][0]["point"] == "3.5"
    assert leg["picks"][0]["opposite"]["american"] == -105        # the under, same line and book
    assert body["cost"] == 1 and body["cached"] is False
    assert body["credits"]["remaining"] == 19_999 and body["credits"]["spent"] == 1
    # the alternate market wasn't needed, so it wasn't bought
    assert [c[2] for c in source.paid_calls] == [("player_receptions",)]


def test_an_explicit_line_and_the_under(client):
    body = ask(client, "Metcalf under 3.5 receptions")
    assert body["message"].startswith("DK Metcalf Under 3.5 receptions pays -105 at Hard Rock")
    assert "$10 would return $19.52" in body["message"]
    over = ask(client, "Fannin over 4.5 receptions")
    assert over["message"].startswith("Harold Fannin Jr. Over 4.5 receptions pays -115")
    assert f"$10 would return ${returns(-115)}" in over["message"]


def test_a_line_the_main_market_lacks_is_found_in_the_alternates_for_one_more_credit(
        client, source):
    body = ask(client, "Metcalf got 5 catches")                   # Over 4.5: not in the main market
    assert body["message"].startswith(
        "DK Metcalf Over 4.5 receptions pays +170 at Hard Rock Bet right now: "
        "$10 would return $27.00")
    assert body["cost"] == 2 and body["credits"]["remaining"] == 19_998
    assert [c[2] for c in source.paid_calls] == [("player_receptions",),
                                                 ("player_receptions_alternate",)]
    assert fixture_price("hardrockbet", "player_receptions_alternate", "DK Metcalf", "Over",
                         4.5) == 170


def test_a_line_nobody_posts_gets_the_nearest_each_side_and_no_invented_price(client):
    body = ask(client, "Metcalf over 12.5 receptions")
    assert body["kind"] == "answer" and body["legs"][0]["exact"] is False
    assert body["message"].startswith("12.5 isn't posted right now. The nearest lines:")
    assert "$10 would return" not in body["message"]              # no price for the asked line
    assert [p["point"] for p in body["legs"][0]["picks"]] == ["8.5"]


def test_market_not_available_when_the_books_dont_post_it_for_the_game(client, source):
    source.drop = {"player_receptions", "player_receptions_alternate"}
    body = ask(client, "Klein got 4 catches")
    assert body["kind"] == "unavailable"
    assert body["message"] == ("Market not available: no book is posting Receptions for this "
                               "game right now.")
    assert body["legs"][0]["available"] is False and body["cost"] == 1  # the call was made


def test_market_not_available_when_the_player_isnt_posted(client):
    body = ask(client, "Klein got 4 catches")
    assert body["kind"] == "unavailable"
    assert body["message"].startswith(
        "Market not available: no book is posting Receptions for \"klein\" in this game")
    assert "pays" not in body["message"]


def test_a_rejected_market_is_market_not_available(client, source):
    source.reject = {"player_receptions": RequestRejected("HTTP 422: unknown market", 422)}
    body = ask(client, "Metcalf got 4 catches")
    assert body["kind"] == "not_offered"
    assert body["message"] == "The Odds API doesn't offer that market for this game."


def test_the_odds_api_down_is_said_and_nothing_is_made_up(client, source):
    from juju.core.enums import FailureKind
    from juju.ingest.http import FetchError
    source.fail = FetchError("u", FailureKind.TRANSIENT, "timeout")
    body = ask(client, "Metcalf got 4 catches")
    assert body["kind"] == "unavailable" and "no price was made up" in body["message"]
    assert "$" not in body["message"]


def test_a_repeat_within_the_cache_window_burns_nothing(client, source):  # cache hit, credits
    first = ask(client, "Metcalf got 4 catches")
    again = ask(client, "Metcalf got 4 catches")
    other = ask(client, "Fannin 5+ receptions")                    # another player, same market
    assert first["cost"] == 1 and again["cost"] == 0 and other["cost"] == 0
    assert again["cached"] is True and other["cached"] is True
    assert first["message"] == again["message"]
    assert len(source.paid_calls) == 1
    assert other["credits"]["remaining"] == again["credits"]["remaining"] == 19_999
    assert other["credits"]["spent"] == 1
    assert again["as_of"] == first["as_of"]                        # the same minute's prices


def test_a_question_after_the_window_asks_again_and_takes_another_credit(
        client, source, clock):                                    # cache miss, credit deduction
    ask(client, "Metcalf got 4 catches")
    clock.advance(30)
    assert ask(client, "Metcalf got 4 catches")["cached"] is True
    clock.advance(31)                                              # 61 seconds since the call
    fresh = ask(client, "Metcalf got 4 catches")
    assert fresh["cached"] is False and fresh["cost"] == 1
    assert fresh["credits"]["remaining"] == 19_998 and fresh["credits"]["spent"] == 2
    assert len(source.paid_calls) == 2


def test_the_cost_is_shown_before_the_question_and_asking_the_plan_is_free(client, source):
    plan = client.get("/api/live/plan", params={
        "game_id": EVENT_ID, "q": "Metcalf got 4 catches"}).json()
    assert plan["cost"]["low"] == 1 and plan["cost"]["high"] == 2
    assert plan["cost"]["text"] == "This will cost 1 to 2 credits."
    td = client.get("/api/live/plan", params={
        "game_id": EVENT_ID, "q": "Warren anytime td"}).json()["cost"]
    assert (td["low"], td["high"], td["text"]) == (1, 1, "This will cost 1 credit.")
    assert source.paid_calls == []                                 # the plan never calls out
    ask(client, "Metcalf got 4 catches")
    after = client.get("/api/live/plan", params={
        "game_id": EVENT_ID, "q": "Fannin over 4.5 receptions"}).json()["cost"]
    assert after["cached"] == 1 and after["low"] == 0       # receptions is in the last minute
    assert after["high"] == 1                               # unless the line is missing: its ladder
    assert after["text"] == "Free unless the line isn't posted: then 1 credit."
    parlay = client.get("/api/live/plan", params={
        "game_id": EVENT_ID, "q": "Metcalf 4 catches and Warren anytime td"}).json()["cost"]
    assert parlay["low"] == 1 and parlay["high"] == 2             # receptions is cached; TD is not


def test_the_credit_meter_warns_when_low_and_never_blocks(client, source):
    source.remaining = 1500
    client.live._remaining = 1500
    body = ask(client, "Metcalf got 4 catches")
    assert body["credits"]["state"] == "low" and body["credits"]["remaining"] == 1499
    assert "1,499 odds credits are left" in body["credits"]["note"]
    assert body["kind"] == "answer"                                # live questions come first
    source.remaining = 1
    client.live._remaining = 1
    out = ask(client, "Fannin got 5 catches")
    assert out["credits"]["state"] in ("low", "out") and out["kind"] == "answer"


def test_other_bets_spreads_moneylines_totals_and_tds(client):
    assert ask(client, "Browns +2.5")["message"].startswith(
        "Cleveland Browns +2.5 pays -105 at Hard Rock Bet right now: $10 would return $19.52")
    ml = ask(client, "Steelers moneyline")["message"]
    assert ml.startswith("Pittsburgh Steelers to win pays -145 at Hard Rock Bet")
    # Hard Rock's total is 38.0; 38.5 is DraftKings' line, so DraftKings leads for it.
    total = ask(client, "game total over 38.5")["message"]
    assert total.startswith("Over 38.5 game total pays -105 at DraftKings")
    td = ask(client, "Warren anytime td")["message"]
    assert td.startswith("Jaylen Warren anytime td pays") and "Hard Rock Bet" in td


def test_the_play_isnt_guessed_when_the_words_name_no_bet_or_no_player(client, source):
    for text, expect in [("Metcalf", "I couldn't tell which bet that is."),
                         ("4 catches", "Which player?"),
                         ("Chiefs -3.5", "Which team? This game is Pittsburgh Steelers at "
                                         "Cleveland Browns.")]:
        body = ask(client, text)
        assert body["kind"] == "ask" and body["message"].startswith(expect)
    assert source.paid_calls == []                                 # asking what you meant is free


# --- Yards with no kind, and where he stands (the database, never prices) ---------------------


@pytest.fixture
def seeded(db):
    with Session(db, expire_on_commit=False) as s:
        game = Game(odds_event_id=EVENT_ID, home_team="Cleveland Browns",
                    away_team="Pittsburgh Steelers", home_abbr="CLE", away_abbr="PIT",
                    home_espn_id="5", away_espn_id="23",
                    commence_time=datetime(2026, 10, 2, 0, 15, tzinfo=UTC))
        s.add_all([game, Player(espn_athlete_id="1", name="DK Metcalf", team_espn_id="23",
                                position="WR"),
                   Player(espn_athlete_id="2", name="Jaylen Warren", team_espn_id="23",
                          position="RB"),
                   Player(espn_athlete_id="3", name="Harold Fannin Jr.", team_espn_id="5",
                          position="TE")])
        s.flush()
        s.add(LiveStat(game_id=game.id, espn_athlete_id="1", stat=Stat.RECEPTIONS,
                       value=Decimal("3.0"), source=DataSource.ESPN_WEB, updated_at=START))
        s.add(LiveStat(game_id=game.id, espn_athlete_id="3", stat=Stat.RUSHING_YARDS,
                       value=Decimal("0"), source=DataSource.ESPN_WEB, updated_at=START))
        s.commit()
        return game.id


def test_yards_with_no_kind_follow_the_players_position(client, seeded):
    body = ask(client, "Metcalf over 43.5 yards")                  # a WR: receiving
    assert body["kind"] == "answer"
    assert body["message"].startswith("DK Metcalf Over 43.5 receiving yards pays")


def test_yards_for_a_running_back_or_an_unknown_player_are_asked_about(client, source, seeded):
    for text in ("Warren over 45.5 yards", "Klein over 45.5 yards"):
        body = ask(client, text)
        assert body["kind"] == "ask"
        assert body["message"].startswith("Receiving, rushing or passing yards?")
    assert source.paid_calls == []


def test_without_the_database_yards_with_no_kind_are_asked_about(client, source):
    assert ask(client, "Josh Downs over 45.5 yards")["kind"] == "ask"
    assert source.paid_calls == []


def test_where_he_stands_comes_from_the_box_score_when_there_is_one(client, seeded):
    body = ask(client, "Metcalf got 4 catches")
    assert body["message"].endswith("DK Metcalf has 3 receptions so far.")
    assert body["legs"][0]["stands"] == "DK Metcalf has 3 receptions so far."


def test_a_player_who_has_played_but_not_caught_one_has_a_real_zero(client, seeded):
    body = ask(client, "Fannin got 5 catches")                    # appears in the box score
    assert body["message"].endswith("Harold Fannin Jr. has 0 receptions so far.")


def test_no_stat_line_means_nothing_is_said_never_a_zero(client, seeded):
    body = ask(client, "Warren anytime td")                       # Warren has no stat rows
    assert "so far" not in body["message"]


def test_without_a_followed_game_nothing_is_said_about_where_he_stands(client):
    assert "so far" not in ask(client, "Metcalf got 4 catches")["message"]


# --- Parlays ----------------------------------------------------------------------------------


def test_a_live_parlay_is_priced_at_one_book_with_the_correlation_note(client, source):
    body = ask(client, "Metcalf got 4 catches and Fannin 5+ receptions")
    assert body["kind"] == "parlay" and len(body["legs"]) == 2
    expected = odds.payout(STAKE, odds.parlay_decimal([-125, -115]))
    assert body["parlay"]["book"] == "hardrockbet"
    assert body["parlay"]["returns"] == format(expected, "f") == "33.65"
    assert "Your 2-leg parlay pays about $33.65 on $10 at Hard Rock Bet" in body["message"]
    assert "Books adjust same-game parlays for correlation" in body["message"]
    assert body["cost"] == 1 and len(source.paid_calls) == 1       # one market, one credit


def test_a_parlay_across_markets_costs_one_credit_per_market(client, source):
    body = ask(client, "Metcalf got 4 catches and Warren anytime td")
    assert body["kind"] == "parlay" and body["cost"] == 2
    assert len(source.paid_calls) == 2
    plan = client.get("/api/live/plan", params={
        "game_id": EVENT_ID, "q": "Fannin got 5 catches, Browns +2.5"}).json()["cost"]
    assert (plan["low"], plan["high"]) == (1, 3)      # receptions is cached: 0, spread 1, alts 2


def test_a_parlay_with_a_leg_that_isnt_posted_is_not_priced(client):
    body = ask(client, "Metcalf got 4 catches and Klein got 4 catches")
    assert body["kind"] == "unavailable" and body["parlay"] is None
    assert "Market not available" in body["message"]
    odd = ask(client, "Metcalf got 4 catches and Fannin over 12.5 receptions")
    assert odd["kind"] == "parlay" and odd["parlay"] is None
    assert "I can't price the parlay: every leg needs a posted line." in odd["message"]


def test_a_parlay_has_two_to_six_legs(client, source):
    seven = " and ".join(["Metcalf got 4 catches"] * 7)
    assert ask(client, seven[:200])["message"].startswith("A parlay has 2 to 6 legs")


# --- The rules that hold the whole thing up ---------------------------------------------------


def test_the_live_chat_never_reads_the_archive():
    """Nothing the live path runs imports the archive's rules or its prices and snapshots (the
    chat has a `Price` of its own, in core/livechat.py: that one is fine)."""
    root = Path(live_chat.__file__).resolve().parents[1]
    for rel in ("api/live_chat.py", "api/live_assistant.py", "core/livechat.py",
                "core/assistant.py", "ingest/live_odds.py", "ingest/assistant.py"):
        tree = ast.parse((root / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names = {a.name for a in node.names}
                assert node.module != "juju.core.t45", rel
                if node.module == "juju.core.models":
                    assert not names & {"Price", "OddsSnapshot", "Lookup"}, (rel, names)
                if node.module == "juju.api.views":
                    assert names == {"BOOK_NAMES"}, (rel, names)
            elif isinstance(node, ast.Import):
                assert not any("t45" in a.name for a in node.names), rel


def test_the_live_chat_answers_with_live_numbers_not_archived_ones(client, db, seeded):
    from juju.core.enums import SnapshotSource
    from juju.core.models import OddsSnapshot, Price
    with Session(db) as s:
        snap = OddsSnapshot(
            game_id=seeded, source=SnapshotSource.LIVE, slot="t45", observed_at=START,
            fetched_at=START, commence_time=START, markets="player_receptions",
            books="hardrockbet", cost=1, payload_sha256="0" * 64, payload_gzip=b"")
        s.add(snap)
        s.flush()
        s.add(Price(snapshot_id=snap.id, game_id=seeded, book="hardrockbet",
                    market_key="player_receptions", outcome_name="Over",
                    description="DK Metcalf", point=Decimal("3.5"), american=7777))
        s.commit()
    body = ask(client, "Metcalf got 4 catches")
    assert "-125" in body["message"] and "7777" not in json.dumps(body)


def test_every_answer_says_it_is_live_and_not_a_sportsbook(client):
    body = ask(client, "Metcalf got 4 catches")
    assert body["disclaimer"] == ("Live prices, as of the time shown. Juju is not a sportsbook "
                                  "and does not take bets.")
    assert datetime.fromisoformat(body["as_of"]) == START


def test_without_an_odds_api_key_the_chat_is_off(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", str(db.url.render_as_string(hide_password=False)))
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    get_settings.cache_clear()
    app_module.app.dependency_overrides.clear()
    r = TestClient(app_module.app).get("/api/live/games")
    assert r.status_code == 503 and "no Odds API key" in r.json()["detail"]
    get_settings.cache_clear()


def test_the_text_is_bounded(client):
    r = client.post("/api/live/ask", json={"game_id": EVENT_ID, "text": "x" * 201})
    assert r.status_code == 422
    assert client.post("/api/live/ask", json={"game_id": EVENT_ID, "text": ""}).status_code == 422


def test_the_dev_fixture_source_runs_the_chat_with_no_key(db, monkeypatch):
    """`LIVE_ODDS_FIXTURE=1` (dev and e2e only, dev_live.py): the recordings, no key, no credits."""
    monkeypatch.setenv("DATABASE_URL", str(db.url.render_as_string(hide_password=False)))
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setenv("LIVE_ODDS_FIXTURE", "1")
    monkeypatch.setattr(live_chat, "_live", None)
    get_settings.cache_clear()
    factory = sessionmaker(bind=db, expire_on_commit=False)

    def session():
        with factory() as s:
            yield s

    app_module.app.dependency_overrides.clear()
    app_module.app.dependency_overrides[app_module.get_session] = session
    c = TestClient(app_module.app)
    games = c.get("/api/live/games").json()["games"]
    assert {g["label"] for g in games} == {"Philadelphia Eagles @ Chicago Bears",
                                           "Pittsburgh Steelers @ Cleveland Browns"}
    phi_chi = "47dc7baa254659f3beb2ed2b38c207b6"
    body = c.post("/api/live/ask", json={"game_id": phi_chi,
                                         "text": "Barkley over 73.5 rushing yards"}).json()
    assert body["message"].startswith(
        "Saquon Barkley Over 73.5 rush yards pays -112 at DraftKings right now")
    assert body["credits"]["remaining"] == 19_999
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()
