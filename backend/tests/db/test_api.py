"""The internal API end to end on the recorded PHI @ CHI game (docs/GOALS.md sections 3, 5, 7)."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from juju.api import app as app_module
from juju.config import get_settings
from juju.dev_seed import seed_phi_chi_scenario
from tests.support import BARKLEY, HURTS, SMITH

pytestmark = pytest.mark.db


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", str(db.url.render_as_string(hide_password=False)))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    get_settings.cache_clear()
    factory = sessionmaker(bind=db, expire_on_commit=False)

    def session():
        with factory() as s:
            yield s

    app_module.app.dependency_overrides[app_module.get_session] = session
    app_module._hot_written.clear()
    app_module._limit = app_module.RateLimit(app_module.LOOKUPS_PER_MINUTE)
    app_module._challenge = app_module.RateLimit(app_module.CHALLENGE_AFTER,
                                                 app_module.CHALLENGE_WINDOW_SECONDS)
    yield TestClient(app_module.app)
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def seed(db, scenario: str) -> int:
    with Session(db, expire_on_commit=False) as s:
        return seed_phi_chi_scenario(s, scenario, datetime.now(tz=UTC)).id


def card(view, key):
    return next(c for c in view["cards"] if c["key"] == key)


def test_live_player_cards_price_first_and_status(client, db):
    game = seed(db, "live")
    r = client.get(f"/api/player/{game}/{BARKLEY}")
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("public, s-maxage=5")
    view = r.json()
    rush = card(view, "player_rush_yds")
    # No Hard Rock in the recording, so the chain falls through to DraftKings.
    assert rush["price"]["book_name"] == "DraftKings" and rush["price"]["american"] == -112
    assert rush["price"]["timing"] == "on_time" and rush["price"]["minutes_before_kickoff"] == 46
    assert rush["line"] == "73.5" and rush["current"] == "82.0"
    assert rush["outcome"] == "locked" and rush["headline"] == "$10 → $18.93"
    assert rush["fair_returns"] is not None and rush["others"]  # other books listed
    assert view["disclaimer"].startswith("Hypothetical")
    assert view["game"]["status_text"].startswith("Q4")


def test_final_game_settles_and_flags_early_prices(client, db):
    game = seed(db, "final")
    view = client.get(f"/api/player/{game}/{SMITH}").json()
    rec = card(view, "player_reception_yds")
    assert rec["price"]["timing"] == "early"  # recorded about two hours before kickoff
    assert rec["outcome"] == "lost" and rec["headline"] == "$10 → $0.00"  # 65 < 71.5
    receptions = card(view, "player_receptions")
    assert receptions["outcome"] == "won"  # 6 > 5.5


def test_a_play_that_is_not_in_the_feed_yet_waits(client, db):
    game = seed(db, "live")
    # "Smith 45-yd catch": his longest in the feed is 30, so the feed is behind.
    view = client.get(f"/api/player/{game}/{SMITH}",
                      params={"play": "catch", "expect": "true", "yards": 45}).json()
    assert view["waiting_for_feed"] is True
    receptions = card(view, "player_receptions")  # 6, line 5.5: already cashed, not waiting
    assert receptions["outcome"] == "locked"
    assert view["cards"][0]["touched"]  # touched markets come first
    # "Smith 30-yd catch" is in the feed already: nothing waits.
    view = client.get(f"/api/player/{game}/{SMITH}",
                      params={"play": "catch", "expect": "true", "yards": 30}).json()
    assert view["waiting_for_feed"] is False
    # "Barkley TD": the feed shows no touchdown for him, so his touched live markets wait.
    view = client.get(f"/api/player/{game}/{BARKLEY}",
                      params={"play": "touchdown", "expect": "true"}).json()
    assert view["waiting_for_feed"] is True


def test_a_named_threshold_shows_its_ladder_card(client, db):
    game = seed(db, "final")
    view = client.get(f"/api/player/{game}/{BARKLEY}", params={"threshold": "79.5"}).json()
    ladder = card(view, "player_rush_yds_alternate")
    assert ladder["bet"] == "80+ rush yards" and ladder["outcome"] == "won"  # 82


def test_team_cards(client, db):
    game = seed(db, "final")
    view = client.get(f"/api/team/{game}/3").json()  # Chicago
    spread = card(view, "spreads")
    assert spread["bet"].startswith("CHI") and spread["outcome"] == "won"  # 27-7
    assert card(view, "totals")["outcome"] == "lost"  # 34 < 42.5


def test_lookup_goes_straight_to_a_clear_player(client, db):
    game = seed(db, "live")
    r = client.post("/api/lookup", json={"text": "Barkley 17 yd run"}).json()
    assert (r["kind"], r["game_id"], r["id"]) == ("player", game, BARKLEY)
    assert r["focus"]["play"] == "run" and r["focus"]["expect"] is True
    r = client.post("/api/lookup", json={"text": "hurts td"}).json()
    assert (r["kind"], r["id"]) == ("player", HURTS)


def test_lookup_offers_choices_when_unsure(client, db):
    seed(db, "live")
    r = client.post("/api/lookup", json={"text": "Smith catch"}).json()
    assert r["kind"] == "choices" and len(r["choices"]) >= 2  # more than one Smith


def test_lookup_finds_a_team(client, db):
    seed(db, "live")
    r = client.post("/api/lookup", json={"text": "Bears cover"}).json()
    assert (r["kind"], r["id"]) == ("team", "3")


def test_several_bets_in_one_line_are_a_parlay(client, db):
    game = seed(db, "live")
    body = client.post("/api/lookup", json={
        "text": "Barkley 100+ rush yds and DeVonta Smith 5+ catches"}).json()
    assert (body["kind"], body["game_id"]) == ("parlay", game)
    assert body["legs"] == (f"p-{BARKLEY}-player_rush_yds_alternate-99.5,"
                            f"p-{SMITH}-player_receptions_alternate-4.5")
    body = client.post("/api/lookup", json={
        "text": "Barkley TD, DeVonta Smith over 71.5 receiving yards & Bears cover"}).json()
    assert body["legs"] == (f"p-{BARKLEY}-player_anytime_td,"
                            f"p-{SMITH}-player_reception_yds_alternate-71.5,t-3-spreads")
    parlay = client.get(f"/api/parlay/{game}", params={"legs": body["legs"]})
    assert parlay.status_code == 200


@pytest.mark.parametrize("text", [
    "Hurts and Smith connect for 30",       # one play, two players: no bets named
    "Smith catch and Barkley TD",           # a shared surname: not certain
    "Barkley TD and Mahomes TD",            # someone not in the game
    "Barkley TD and Barkley touchdown",     # the same bet twice
])
def test_a_line_that_is_not_certainly_a_parlay_is_one_lookup(client, db, text):
    seed(db, "live")
    assert client.post("/api/lookup", json={"text": text}).json()["kind"] != "parlay"


def test_lookups_are_rate_limited(client, db):
    seed(db, "live")
    app_module._limit = app_module.RateLimit(2)
    codes = [client.post("/api/lookup", json={"text": "Barkley"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_past_the_threshold_the_site_is_asked_for_a_turnstile_check(client, db):
    seed(db, "live")
    app_module._challenge = app_module.RateLimit(2, 900)
    site = {"x-juju-challenge": "turnstile"}
    codes = [client.post("/api/lookup", json={"text": "Barkley"}, headers=site).status_code
             for _ in range(3)]
    assert codes == [200, 200, 428]
    r = client.post("/api/lookup", json={"text": "Barkley"}, headers=site)
    assert r.json()["challenge"] == "turnstile" and r.headers["cache-control"] == "no-store"
    # A person who passed goes through, and isn't counted towards the next check.
    verified = {**site, "x-juju-verified": "1"}
    assert client.post("/api/lookup", json={"text": "Barkley"},
                       headers=verified).status_code == 200
    # Without Turnstile on the site nothing is asked: the per-minute limit still holds.
    assert client.post("/api/lookup", json={"text": "Barkley"}).status_code == 200


def test_suggest_prefers_players_in_play(client, db):
    seed(db, "live")
    choices = client.get("/api/suggest", params={"q": "sa"}).json()["choices"]
    assert choices[0]["name"] == "Saquon Barkley" or any(
        c["name"] == "Saquon Barkley" for c in choices)


def test_live_lists_games_and_plays_without_prices(client, db):
    seed(db, "live")
    body = client.get("/api/live").json()
    g = body["games"][0]
    assert g["label"] == "PHI @ CHI" and g["plays"]
    assert all("american" not in str(p) for p in g["plays"])
    assert any(p["label"] == "J. Hurts 20-yd run" for p in g["plays"])


def week_of_games(client, db, monkeypatch, now: datetime, final: tuple[str, ...] = ()) -> dict:
    """The recorded week (Mon 9/28 to Mon 10/5, 17 games) as the Odds API lists it, the home
    teams in `final` finished, and /api/live at `now`."""
    from juju.core.enums import EventStatus
    from juju.core.models import Game
    from juju.worker.schedule import upsert_odds_events
    from tests.support import FakeOdds
    with Session(db) as s:
        upsert_odds_events(s, FakeOdds().events(), now)
        for g in s.query(Game).filter(Game.home_team.in_(final)):
            g.status = EventStatus.FINAL
        s.commit()
    monkeypatch.setattr(app_module, "_utcnow", lambda: now)
    return client.get("/api/live").json()


def homes(body: dict) -> list[str]:
    return [g["home"]["name"] for g in body["games"]]


def test_the_home_page_lists_this_weeks_games(client, db, monkeypatch):
    friday = datetime(2026, 10, 2, 16, tzinfo=UTC)  # noon ET; Thursday's game is over
    body = week_of_games(client, db, monkeypatch, friday, final=("Cleveland Browns",))
    assert len(body["games"]) == 16  # Monday 9/28 was last week
    assert homes(body)[-1] == "Cleveland Browns" and body["games"][-1]["status"] == "final"
    kickoffs = [g["commence_time"] for g in body["games"][:-1]]
    assert kickoffs == sorted(kickoffs) and homes(body)[-2] == "New Orleans Saints"
    assert body["next_kickoff"]["commence_time"].startswith("2026-10-04T13:30")  # 9:30 am ET
    assert all(g["plays"] == [] for g in body["games"])  # plays only for games in play


def test_a_game_nothing_updated_since_kickoff_is_not_listed_as_next(client, db, monkeypatch):
    body = week_of_games(client, db, monkeypatch, datetime(2026, 10, 2, 16, tzinfo=UTC))
    assert "Cleveland Browns" not in homes(body)  # still "scheduled" 16 hours after kickoff


def test_on_tuesday_morning_monday_nights_game_stays_and_the_week_turns(client, db, monkeypatch):
    tuesday = datetime(2026, 10, 6, 13, tzinfo=UTC)  # 9 am ET
    body = week_of_games(client, db, monkeypatch, tuesday, final=("New Orleans Saints",))
    assert homes(body) == ["New Orleans Saints"]


def test_after_monday_night_the_next_kickoff_is_still_named(client, db, monkeypatch):
    monday_night = datetime(2026, 9, 29, 3, 30, tzinfo=UTC)  # 11:30 pm ET, PHI @ CHI over
    body = week_of_games(client, db, monkeypatch, monday_night, final=("Chicago Bears",))
    assert homes(body) == ["Chicago Bears"]  # Thursday's game is next week's
    assert body["next_kickoff"]["commence_time"].startswith("2026-10-02T00:15")


def test_a_finished_game_lists_no_plays(client, db, monkeypatch):
    seed(db, "final")  # PHI @ CHI as recorded: kickoff Mon 9/28 8:15 pm ET
    monkeypatch.setattr(app_module, "_utcnow", lambda: datetime(2026, 9, 29, 8, tzinfo=UTC))
    games = client.get("/api/live").json()["games"]
    assert games and all(g["plays"] == [] for g in games)


def test_there_is_no_endpoint_that_lists_prices_across_players(client):
    # The parlay spans players: only the legs someone picked, at one book, in one game
    # (tests/db/test_parlay.py holds it to that).
    # /api/game lists one game's plays and players, with no prices (the test below).
    paths = {r.path for r in app_module.app.routes}
    assert paths == {"/health", "/api/status", "/api/live", "/api/suggest", "/api/lookup",
                     "/api/game/{game_id}", "/api/player/{game_id}/{athlete_id}",
                     "/api/team/{game_id}/{team_id}", "/api/parlay/{game_id}"}


def test_a_game_lists_its_plays_and_players_without_prices(client, db):
    game_id = seed(db, "live")
    body = client.get(f"/api/game/{game_id}").json()
    assert body["label"] == "PHI @ CHI" and body["status"] == "in_progress"
    assert body["plays"][0]["label"] == "J. Hurts 20-yd run"  # newest first
    assert any(p["kind"] == "def_interception" and p["passer_id"] for p in body["plays"])
    assert {"Saquon Barkley", "DeVonta Smith"} <= {p["name"] for p in body["players"]}
    text = str(body)
    assert "american" not in text and "book" not in text and "$" not in text
    assert client.get("/api/game/999999").status_code == 404


def test_health_reports_the_worker(client, db):
    from juju.worker.jobs import heartbeat
    heartbeat(db, datetime.now(tz=UTC) - timedelta(minutes=10))
    body = client.get("/health").json()
    assert body["ok"] is False and body["worker_heartbeat_seconds_ago"] >= 600


NOW = datetime(2026, 10, 15, 22, 0, tzinfo=UTC)  # credits reset on the 1st


def _odds_api(db, *, quota: int | None, ago: timedelta | None,
              read: datetime | None = NOW) -> None:
    from juju.core.models import SourceHealth
    with Session(db) as s:
        s.merge(SourceHealth(source="odds_api", quota_remaining=quota, quota_at=read,
                             last_success_at=None if ago is None else NOW - ago,
                             consecutive_failures=0, requests_last_hour=0, errors_last_hour=0))
        s.commit()


@pytest.mark.parametrize("quota, ago, read, prices", [
    (None, None, None, "off"),                       # no key: never a successful call
    (20_000, timedelta(hours=3), NOW, "off"),        # a key, but nothing heard for hours
    (20_000, timedelta(minutes=5), NOW, "on"),
    (None, timedelta(minutes=5), None, "on"),        # quota not known yet
    (500, timedelta(minutes=5), NOW, "rationed"),    # a free key's month
    (2, timedelta(minutes=5), NOW, "paused"),
    (0, timedelta(minutes=5), NOW, "paused"),
    (0, timedelta(minutes=5), datetime(2026, 9, 30, tzinfo=UTC), "on"),  # last month's 0
    (500, timedelta(minutes=5), None, "on"),         # a count from before 0009: whose? unknown
])
def test_the_status_says_whether_new_games_get_prices(client, db, monkeypatch, quota, ago, read,
                                                      prices):
    monkeypatch.setattr(app_module, "_utcnow", lambda: NOW)
    _odds_api(db, quota=quota, ago=ago, read=read)
    body = client.get("/api/status").json()
    assert body["prices"] == prices
    assert (body["prices_message"] is None) is (prices == "on")


def test_after_a_key_change_the_status_says_prices_are_on(client, db, monkeypatch):
    from juju.ingest.odds_api import OddsApiClient, key_fingerprint
    from juju.ingest.router import Breakers
    from juju.worker.jobs import heartbeat
    monkeypatch.setattr(app_module, "_utcnow", lambda: NOW)
    old = Breakers(db, clock=lambda: NOW)
    old.hold_key("odds_api", key_fingerprint("old-key"))
    old.success("odds_api", quota_remaining=0)  # the free key, used up this month
    old.persist()
    assert client.get("/api/status").json()["prices"] == "paused"
    restarted = Breakers(db, clock=lambda: NOW)  # the worker, after .env got the new key
    restarted.load()
    OddsApiClient("new-key", restarted, clock=lambda: NOW)
    heartbeat(db, NOW, breakers=restarted)
    assert client.get("/api/status").json()["prices"] == "on"


def test_with_no_odds_api_row_at_all_prices_are_off(client):
    assert client.get("/api/status").json()["prices"] == "off"
