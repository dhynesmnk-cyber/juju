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


def test_lookups_are_rate_limited(client, db):
    seed(db, "live")
    app_module._limit = app_module.RateLimit(2)
    codes = [client.post("/api/lookup", json={"text": "Barkley"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


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


def test_there_is_no_endpoint_that_lists_prices_across_players(client):
    paths = {r.path for r in app_module.app.routes}
    assert paths == {"/health", "/api/status", "/api/live", "/api/suggest", "/api/lookup",
                     "/api/player/{game_id}/{athlete_id}", "/api/team/{game_id}/{team_id}"}


def test_health_reports_the_worker(client, db):
    from juju.worker.jobs import heartbeat
    heartbeat(db, datetime.now(tz=UTC) - timedelta(minutes=10))
    body = client.get("/health").json()
    assert body["ok"] is False and body["worker_heartbeat_seconds_ago"] >= 600
