"""The tracker's API, /api/me (docs/plans/integrate-parlaytracker.md sections 6.5 and 7), end to
end on the test database, with ESPN answered from recorded fixtures.

Several cases are parlaytracker's page tests (tests/app/ at c3bd43c), now asked of the API that
feeds the pages: the Live page's (here the Watch page's), Analytics' and the Log form's.
"""
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from juju.api import app as app_module
from juju.api import me
from juju.config import get_settings
from juju.core.enums import FailureKind, HealthState
from juju.core.models import SourceHealth
from juju.ingest import espn
from juju.ingest.http import FetchError
from juju.tracker import services
from juju.tracker.models import (
    Event,
    EventStatus,
    Leg,
    LegResult,
    LegSource,
    Slip,
    SlipStatus,
    Sport,
    Sportsbook,
    Tag,
)
from juju.tracker.pickers import Pickers
from juju.tracker.schemas import SlipIn
from tests.support import FIXTURES

pytestmark = pytest.mark.db

GAME = "401872963"  # PHI @ CHI in the recorded scheduled scoreboard: Mon 28 Sep, 8:15 pm ET
DAY = "2026-09-28"
KICKOFF = datetime(2026, 9, 29, 0, 15, tzinfo=UTC)
BEFORE = KICKOFF - timedelta(hours=6)
USERS = "Chris:first-long-passcode,Ash:second-long-passcode"


def _fixture(name: str):
    return json.loads((FIXTURES / "espn" / name).read_text())


class FakeEspn:
    """The recorded Monday scoreboard and rosters, counting calls; `down` makes ESPN fail."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.down = False

    def scoreboard(self, sport, day):
        self.calls.append(("board", sport, day))
        if self.down:
            raise FetchError("nfl/scoreboard", FailureKind.BLOCKED, "403")
        if sport is Sport.NFL and day == date(2026, 9, 28):
            return espn.parse_scoreboard(sport, _fixture("nfl_scoreboard_2026-09-28_scheduled.json")
                                         ).games
        return []

    def roster(self, sport, team_id):
        self.calls.append(("roster", sport, team_id))
        return espn.parse_roster(_fixture(f"nfl_roster_{team_id}.json"))


@pytest.fixture
def fake_espn(monkeypatch) -> FakeEspn:
    fake = FakeEspn()
    monkeypatch.setattr(me, "_pickers", Pickers(fake.scoreboard, fake.roster))
    return fake


@pytest.fixture
def clock(monkeypatch):
    """Monday afternoon, before PHI @ CHI; a test may move it."""
    moment = {"now": BEFORE}
    monkeypatch.setattr(me, "_utcnow", lambda: moment["now"])
    return moment


def _settings(monkeypatch, db, users: str | None) -> None:
    monkeypatch.setenv("DATABASE_URL", str(db.url.render_as_string(hide_password=False)))
    if users is None:
        monkeypatch.delenv("TRACKER_USERS", raising=False)
    else:
        monkeypatch.setenv("TRACKER_USERS", users)
    monkeypatch.setenv("DISPLAY_TZ", "America/New_York")
    monkeypatch.delenv("MIN_SAMPLE", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def api(db, monkeypatch, fake_espn, clock):
    """A client signed in as Chris (the site names the user; any case will do)."""
    _settings(monkeypatch, db, USERS)
    factory = sessionmaker(bind=db, expire_on_commit=False)

    def session():
        with factory() as s:
            yield s

    app_module.app.dependency_overrides[app_module.get_session] = session
    yield TestClient(app_module.app, headers={"x-juju-user": "chris"})
    app_module.app.dependency_overrides.clear()
    get_settings.cache_clear()


def as_user(api: TestClient, name: str) -> TestClient:
    return TestClient(app_module.app, headers={"x-juju-user": name})


def leg(market="game_total", line="44.5", odds=-110, **kw) -> dict:
    return {"sport": "nfl", "day": DAY, "espn_event_id": GAME, "market_type": market,
            "line": line, "american_odds": odds, **kw}


def book_id(db, name="DraftKings") -> int:
    with Session(db) as s:
        return s.scalars(select(Sportsbook.id).where(Sportsbook.name == name)).one()


def draft(db, legs: list[dict], **kw) -> dict:
    return {"legs": legs, "sportsbook_id": book_id(db), **kw}


def count(db, model) -> int:
    with Session(db) as s:
        return len(s.scalars(select(model)).all())


# --- Who may ask ---------------------------------------------------------------------------------


def private_routes() -> list[tuple[str, str]]:
    paths = app_module.app.openapi()["paths"]
    return [(method.upper(), re.sub(r"\{\w+\}", "1", path.replace("{event_id}", GAME)))
            for path, methods in paths.items() if path.startswith("/api/me/")
            for method in methods]


def test_every_route_is_off_without_tracker_users(db, monkeypatch, fake_espn):
    _settings(monkeypatch, db, None)
    client = TestClient(app_module.app, headers={"x-juju-user": "Chris"})
    routes = private_routes()
    assert len(routes) == 20
    for method, path in routes:
        r = client.request(method, path, json={})
        assert r.status_code == 404, (method, path)
        assert r.headers["cache-control"] == "no-store"
    get_settings.cache_clear()


@pytest.mark.parametrize("headers", [{}, {"x-juju-user": ""}, {"x-juju-user": "mallory"},
                                     {"x-juju-user": "Chris:first-long-passcode"}])
def test_every_route_needs_one_of_the_users(api, headers):
    client = TestClient(app_module.app, headers=headers)
    for method, path in private_routes():
        r = client.request(method, path, json={})
        assert r.status_code == 401, (method, path)
        assert r.json()["detail"] == me.LOCKED


def test_health_names_the_user_as_configured_and_shows_the_banner(api, db):
    body = api.get("/api/me/health").json()
    assert body["user"] == "Chris" and body["review_count"] == 0
    assert body["banners"] == [{"level": "warning",
                                "text": "The background worker has never run on this database."}]
    with Session(db) as s:
        s.add(SourceHealth(source="worker", state=HealthState.OK, last_success_at=BEFORE))
        s.commit()
    assert api.get("/api/me/health").json()["banners"] == []


def test_answers_are_never_cached(api):
    for path in ("/api/me/health", "/api/me/live", "/api/me/slips", "/api/me/review",
                 "/api/me/analytics", f"/api/me/games?date={DAY}"):
        assert api.get(path).headers["cache-control"] == "no-store", path
    for r in (api.get("/api/me/slips/999"), api.get("/api/me/nothing"),
              api.post("/api/me/slips", json={})):  # FastAPI's own 405, 404 and 422 too
        assert r.status_code in (404, 405, 422) and r.headers["cache-control"] == "no-store"


# --- The Log form ---------------------------------------------------------------------------------


def test_games_for_a_day_with_the_sports_markets(api, fake_espn):
    body = api.get("/api/me/games", params={"sport": "nfl", "date": DAY}).json()
    (game,) = body["games"]
    assert game["espn_event_id"] == GAME and game["label"] == "PHI @ CHI · Mon 28 Sep 20:15"
    assert (game["home"]["abbreviation"], game["away"]["abbreviation"]) == ("CHI", "PHI")
    assert {"key": "player_receptions", "label": "Receptions"} in body["markets"]
    assert body["problem"] is None
    api.get("/api/me/games", params={"sport": "nfl", "date": DAY})
    assert fake_espn.calls.count(("board", Sport.NFL, date(2026, 9, 28))) == 1  # cached


def test_when_espn_fails_the_form_says_so_and_nothing_is_kept(api, fake_espn):
    fake_espn.down = True
    body = api.get("/api/me/games", params={"date": DAY}).json()
    assert body["games"] == [] and body["problem"].startswith("Couldn't load NFL games from ESPN")
    fake_espn.down = False
    assert len(api.get("/api/me/games", params={"date": DAY}).json()["games"]) == 1


def test_the_roster_lists_both_teams_away_first(api):
    players = api.get(f"/api/me/roster/{GAME}", params={"date": DAY}).json()["players"]
    labels = [p["label"] for p in players]
    first_home = next(n for n, label in enumerate(labels) if label.endswith(" · CHI")
                      or " · CHI · " in label)
    assert all(" · PHI" in label for label in labels[:first_home])
    assert any(p["name"] == "Saquon Barkley" for p in players)
    assert api.get("/api/me/roster/999", params={"date": DAY}).status_code == 404


def test_a_preview_works_out_the_slip_and_writes_nothing(api, db):
    single = api.post("/api/me/slips/preview", json=draft(db, [leg()])).json()
    assert (single["slip_type"], single["american_odds"], single["errors"]) == (
        "single", -110, [])
    sgp = api.post("/api/me/slips/preview",
                   json=draft(db, [leg(), leg(line="50.5", odds=None)])).json()
    assert sgp["slip_type"] == "sgp" and sgp["errors"] == [
        "Enter the slip's odds as shown by the sportsbook"]
    parlay = api.post("/api/me/slips/preview",
                      json=draft(db, [leg(), leg(line="50.5", odds=150)], sgp=False)).json()
    assert parlay["slip_type"] == "parlay"
    assert (parlay["computed_odds"], parlay["computed_odds_text"]) == (377, "+377")
    assert parlay["american_odds"] == 377 and parlay["errors"] == []
    assert count(db, Event) == 0 and count(db, Slip) == 0


def test_a_preview_says_what_is_missing(api, db):
    no_game = api.post("/api/me/slips/preview",
                       json=draft(db, [leg(espn_event_id=None)])).json()
    assert no_game["errors"] == ["Leg 1: choose a game"]
    stranger = api.post("/api/me/slips/preview", json=draft(db, [
        leg(market="player_receptions", line="5.5", espn_athlete_id="99999999")])).json()
    assert stranger["errors"] == ["Leg 1: that player isn't on either team's roster"]
    no_line = api.post("/api/me/slips/preview", json=draft(db, [leg(line=None)])).json()
    assert no_line["errors"] == ["Leg 1: line is required"]


def test_logging_a_slip_stores_it_as_the_signed_in_user(api, db):
    barkley = next(p for p in api.get(f"/api/me/roster/{GAME}", params={"date": DAY}).json()
                   ["players"] if p["name"] == "Saquon Barkley")
    r = api.post("/api/me/slips", json=draft(db, [
        leg(market="player_rushing_yards", line="84.5", odds=-115,
            espn_athlete_id=barkley["espn_athlete_id"],
            side="home")],  # a side on a player leg is dropped, as the form never showed it
        is_placed=True, stake="10", notes="  hunch  "))
    assert r.status_code == 201, r.json()
    slip = r.json()["slip"]
    assert slip["logged_by"] == "Chris" and slip["status"] == "pending"
    assert slip["summary"] == "Single -115 · DraftKings · $10.00 to win · logged by Chris"
    (only,) = slip["legs"]
    assert only["summary"] == ("Philadelphia Eagles @ Chicago Bears · Rushing yards · "
                               "Saquon Barkley o84.5 -115")
    assert only["player_name"] == "Saquon Barkley" and only["side"] is None
    assert slip["notes"] == "hunch" and r.json()["warnings"] == []
    recent = api.get("/api/me/slips").json()["slips"]
    assert [s["id"] for s in recent] == [slip["id"]]
    with Session(db) as s:
        event = s.scalars(select(Event)).one()
        assert (event.espn_event_id, event.home_team, event.start_time) == (
            GAME, "Chicago Bears", KICKOFF)


def test_the_same_selection_logged_again_warns_who_has_it(api, db):
    assert api.post("/api/me/slips", json=draft(db, [leg()])).status_code == 201
    preview = as_user(api, "Ash").post("/api/me/slips/preview", json=draft(db, [leg()])).json()
    assert preview["warnings"] == ["Leg 1: already logged by Chris at -110."]


def test_a_game_already_started_is_a_warning_not_an_error(api, db, clock):
    clock["now"] = KICKOFF + timedelta(minutes=5)
    r = api.post("/api/me/slips", json=draft(db, [leg()]))
    assert r.status_code == 201
    assert r.json()["warnings"] == ["Leg 1: this game has already started"]


def test_a_slip_that_isnt_valid_is_refused_with_the_reasons_and_nothing_stored(api, db):
    r = api.post("/api/me/slips", json=draft(db, [leg()], is_placed=True))  # no stake
    assert r.status_code == 422
    assert r.json()["detail"] == "a placed bet needs a stake"
    r = api.post("/api/me/slips", json={**draft(db, [leg()]), "sportsbook_id": 9999})
    assert r.status_code == 422 and r.json()["detail"] == "unknown sportsbook 9999"
    assert count(db, Slip) == 0 and count(db, Event) == 0


# --- Settling and correcting by hand --------------------------------------------------------------


def logged(api, db, legs=None, **kw) -> dict:
    r = api.post("/api/me/slips", json=draft(db, legs or [leg()], **kw))
    assert r.status_code == 201, r.json()
    return r.json()["slip"]


def test_a_leg_settles_from_its_final_value_and_a_contradiction_is_refused(api, db):
    slip = logged(api, db, is_placed=True, stake="10")
    leg_id = slip["legs"][0]["id"]
    r = api.patch(f"/api/me/legs/{leg_id}",
                  json={"action": "settle", "final_value": "40", "result": "win"})
    assert r.status_code == 400
    assert r.json()["detail"] == "a final value of 40 makes this leg a loss"
    r = api.patch(f"/api/me/legs/{leg_id}", json={"action": "settle", "final_value": "50"})
    body = r.json()["slip"]
    assert (body["status"], body["payout"], body["payout_text"]) == ("win", "19.09", "$19.09")
    assert body["legs"][0]["settlement_source"] == "manual"
    body = api.patch(f"/api/me/legs/{leg_id}", json={"action": "reopen"}).json()["slip"]
    assert (body["status"], body["legs"][0]["result"]) == ("pending", "pending")


def test_payout_and_cash_out(api, db):
    sgp = logged(api, db, [leg(odds=None), leg(line="50.5", odds=None)], american_odds=450,
                 is_placed=True, stake="10")
    r = api.post(f"/api/me/slips/{sgp['id']}/payout", json={"amount": "35"})
    assert r.status_code == 400 and r.json()["detail"] == "settle every leg first"
    for leg_ in sgp["legs"]:
        api.patch(f"/api/me/legs/{leg_['id']}", json={"action": "settle", "result": "win"})
    body = api.post(f"/api/me/slips/{sgp['id']}/payout", json={"amount": "35"}).json()["slip"]
    assert (body["status"], body["payout"]) == ("win", "35.00")
    other = logged(api, db, [leg(line="30.5")], is_placed=True, stake="5")
    body = api.post(f"/api/me/slips/{other['id']}/cash-out", json={"amount": "7.5"}).json()["slip"]
    assert (body["status"], body["payout"]) == ("cashed_out", "7.50")
    assert api.get("/api/me/slips", params={"view": "open"}).json()["slips"] == []
    assert api.post("/api/me/slips/9999/cash-out", json={"amount": "1"}).status_code == 404


def test_a_closing_line_by_hand_is_stored_but_never_shown(api, db):
    slip = logged(api, db)
    leg_id = slip["legs"][0]["id"]
    r = api.post(f"/api/me/legs/{leg_id}/closing",
                 json={"closing_line": "45.5", "closing_odds": -112,
                       "closing_opposite_odds": -108})
    assert r.status_code == 200 and r.json()["slip"]["legs"][0]["has_closing_line"] is True
    assert "-112" not in r.text and "closing_odds" not in r.text
    with Session(db) as s:
        stored = s.get(Leg, leg_id)
        assert (stored.closing_line, stored.closing_odds, stored.closing_source.value) == (
            D("45.5"), -112, "manual")
    bad = api.post(f"/api/me/legs/{leg_id}/closing",
                   json={"closing_line": "45.25", "closing_odds": -110})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "a line must be a whole or half number"


def test_a_legs_tags_can_be_replaced(api, db):
    wind = api.post("/api/me/settings/tags", json={"category": "Weather", "name": "wind"}).json()
    slip = logged(api, db, [leg(tag_ids=[wind["tag"]["id"]])])
    leg_id = slip["legs"][0]["id"]
    assert slip["legs"][0]["tag_ids"] == [wind["tag"]["id"]]
    body = api.patch(f"/api/me/legs/{leg_id}", json={"action": "tags", "tag_ids": []}).json()
    assert body["slip"]["legs"][0]["tag_ids"] == []
    r = api.patch(f"/api/me/legs/{leg_id}", json={"action": "tags", "tag_ids": [404]})
    assert r.status_code == 400 and r.json()["detail"] == "unknown tag(s) [404]"


def test_review_asks_for_a_result_once_the_game_should_be_over(api, db, clock):
    slip = logged(api, db, is_placed=True, stake="10")
    assert api.get("/api/me/review").json()["empty_text"] == "Nothing to review."
    clock["now"] = KICKOFF + timedelta(hours=5)
    body = api.get("/api/me/review").json()
    kinds = [(i["kind"], i["title"]) for i in body["items"]]
    assert kinds == [("result", "Result needed"), ("closing_line", "Closing line missing")]
    assert body["items"][0]["leg_id"] == slip["legs"][0]["id"] and body["count"] == 1
    assert api.get("/api/me/health").json()["review_count"] == 1


# --- Watch (parlaytracker tests/app/test_live_page.py) --------------------------------------------

NOW = KICKOFF + timedelta(hours=1, minutes=12)  # deep in the second quarter


def in_play(db, slips: list[list[dict]], **fields) -> None:
    """PHI @ CHI under way, 17 - 10 to the home team, polled 12 s ago, with these slips."""
    defaults = dict(status=EventStatus.IN_PROGRESS, period=2, clock_seconds=522, home_score=17,
                    away_score=10, last_polled_at=NOW - timedelta(seconds=12),
                    last_progress_at=NOW - timedelta(seconds=12))
    with Session(db, expire_on_commit=False) as s:
        event = services.upsert_event(
            s, sport=Sport.NFL, espn_event_id=GAME, start_time=KICKOFF,
            home_team="Chicago Bears", away_team="Philadelphia Eagles", home_espn_team_id="3",
            away_espn_team_id="21", status=EventStatus.SCHEDULED)
        for key, value in {**defaults, **fields}.items():
            setattr(event, key, value)
        book = s.scalars(select(Sportsbook).where(Sportsbook.name == "DraftKings")).one()
        for legs in slips:
            single = len(legs) == 1
            slip = services.create_slip(s, SlipIn(
                is_placed=True, stake="10.00", slip_type="single" if single else "sgp",
                sportsbook_id=book.id, american_odds=-110, source="quick_add",
                legs=[{"event_id": event.id, "american_odds": -110 if single else None,
                       **{k: v for k, v in leg_.items() if k != "live_value"}}
                      for leg_ in legs]), "Chris")
            for row, leg_ in zip(slip.legs, legs, strict=True):
                if "live_value" in leg_:
                    row.live_value, row.live_source = D(leg_["live_value"]), LegSource.ESPN_WEB
        s.commit()


def test_nothing_to_watch_says_so(api, clock):
    clock["now"] = NOW
    body = api.get("/api/me/live").json()
    assert body["cards"] == [] and body["empty_text"].startswith("Nothing to watch right now")


def test_a_single_shows_its_live_value_over_or_under_and_the_clock(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "game_total", "line": "25.5", "live_value": "27"}]])
    (card,) = api.get("/api/me/live").json()["cards"]
    (row,) = card["legs"]
    assert (row["standing"], row["status_text"], row["state"]) == ("27 · over", "Q2 8:42", "over")
    assert card["updated_text"] == "updated 12 s ago · via espn_web"
    assert (card["severity"], card["no_change_text"]) == ("ok", None)


def test_the_score_gives_a_team_leg_its_value_and_a_spread_covers(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "alt_spread", "side": "home", "line": "-6.5"}]])
    (card,) = api.get("/api/me/live").json()["cards"]
    assert card["legs"][0]["standing"] == "7 · covering"


def test_a_parlay_shows_its_progress(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "game_total", "line": "20.5", "live_value": "27"},
                  {"market_type": "player_receptions", "espn_athlete_id": "7", "line": "5.5"}]])
    (card,) = api.get("/api/me/live").json()["cards"]
    assert card["progress"] == "1 of 2 over"
    assert card["legs"][1]["standing"] == "no stat line yet"


def test_no_change_for_minutes_and_a_stale_feed(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "game_total", "line": "25.5"}]],
            last_polled_at=NOW - timedelta(minutes=6),
            last_progress_at=NOW - timedelta(minutes=9, seconds=30))
    (card,) = api.get("/api/me/live").json()["cards"]
    assert (card["severity"], card["no_change_text"]) == ("red", "No change for 9 min")


def test_when_every_espn_breaker_is_open_the_page_says_live_data_is_unavailable(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "game_total", "line": "25.5"}]])
    with Session(db) as s:
        for source in ("espn_web", "espn_site", "espn_cdn"):
            s.add(SourceHealth(source=source, state=HealthState.OPEN,
                               failure_kind=FailureKind.BLOCKED,
                               last_failure_at=NOW - timedelta(minutes=8),
                               open_until=NOW + timedelta(minutes=2)))
        s.commit()
    text = api.get("/api/me/live").json()["espn_unavailable_text"]
    assert text == ("Live data unavailable since Mon 28 Sep 21:19 (ESPN is blocking or "
                    "erroring). Results will still settle after the game.")


def test_settled_slips_show_their_result_and_verification(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "game_total", "line": "20.5"}],
                 [{"market_type": "game_total", "line": "21.5"}]])
    with Session(db) as s:
        first, second = s.scalars(select(Leg).order_by(Leg.id)).all()
        services.settle_leg_manually(s, first, final_value=D("30"), now=NOW)
        services.settle_leg_manually(s, second, final_value=D("30"), now=NOW)
        second.settlement_source = LegSource.ESPN_WEB
        s.commit()
    rows = api.get("/api/me/slips", params={"view": "settled"}).json()["slips"]
    assert {(r["status"], r["legs"][0]["verification"]) for r in rows} == {
        ("win", "unverified"), ("win", "awaiting verification")}
    clock["now"] = NOW + timedelta(days=8)
    body = api.get("/api/me/slips", params={"view": "settled"}).json()
    assert body["slips"] == [] and body["empty_text"] == "Nothing has settled in the last 7 days."


def test_watching_never_settles_anything(api, db, clock):
    clock["now"] = NOW
    in_play(db, [[{"market_type": "game_total", "line": "10.5", "live_value": "27"}]])
    api.get("/api/me/live")
    with Session(db) as s:
        assert s.scalars(select(Leg)).one().result is LegResult.PENDING
        assert s.scalars(select(Slip)).one().status is SlipStatus.PENDING


# --- Analytics (parlaytracker tests/app/test_analytics_page.py) -----------------------------------

START = datetime(2026, 9, 27, 20, 5, tzinfo=UTC)


def seed_analytics(db) -> None:
    """Three settled singles and one pending, on two games: total 45.5 wins and closes at 47.5;
    total 40.5 loses; a +120 rushing prop wins and closes at the same line, -120 / +100."""
    with Session(db) as session:
        book = session.scalars(select(Sportsbook).where(Sportsbook.name == "DraftKings")).one()
        events = [services.upsert_event(
            session, sport=Sport.NFL, espn_event_id=f"4019{n}",
            start_time=START + timedelta(days=n),
            home_team="Chicago Bears", away_team="Philadelphia Eagles", home_espn_team_id="3",
            away_espn_team_id="21", status=EventStatus.SCHEDULED) for n in (0, 1)]

        def single(event, leg_, odds=-110):
            return services.create_slip(session, SlipIn(
                is_placed=True, stake="10", slip_type="single", sportsbook_id=book.id,
                american_odds=odds, source="quick_add",
                legs=[{"event_id": event.id, "american_odds": odds, **leg_}]), "Chris")

        a = single(events[0], {"market_type": "game_total", "line": "45.5"})
        services.settle_leg_manually(session, a.legs[0], final_value=D("50"))
        services.set_closing_line(session, a.legs[0], closing_line=D("47.5"), closing_odds=-110)
        b = single(events[1], {"market_type": "game_total", "line": "40.5"})
        services.settle_leg_manually(session, b.legs[0], final_value=D("30"))
        c = single(events[0], {"market_type": "player_rushing_yards", "espn_athlete_id": "7",
                               "line": "55.5"}, odds=120)
        services.settle_leg_manually(session, c.legs[0], final_value=D("60"))
        services.set_closing_line(session, c.legs[0], closing_line=D("55.5"), closing_odds=-120,
                                  closing_opposite_odds=100)
        single(events[1], {"market_type": "game_total", "line": "60.5"})  # pending
        session.commit()


def test_an_empty_database_says_so(api):
    body = api.get("/api/me/analytics").json()
    assert body["empty_text"].startswith("Nothing logged yet")
    assert body["selections"]["rows"] == []


def test_the_selection_view_gives_clv_then_the_grouped_rows(api, db):
    seed_analytics(db)
    body = api.get("/api/me/analytics").json()["selections"]
    price_clv = (((120 / 220) / (120 / 220 + 0.5)) - 100 / 220) * 100
    assert body["price_clv"] == {"text": f"{price_clv:+.2f} pp", "n": 1}
    assert body["line_clv"] == {"text": "+2.0 pts", "n": 1}
    (row,) = body["rows"]
    assert (body["group_label"], row["label"], row["n"]) == ("Sport", "nfl", 3)
    assert row["hit_rate"].startswith("66.7% (") and row["low_sample"] is True
    assert row["price_clv"].endswith("(n 1)") and row["line_clv"] == "+2.0 pts (n 1)"


def test_grouping_and_filters(api, db):
    seed_analytics(db)
    by_market = api.get("/api/me/analytics", params={"group": "market"}).json()["selections"]
    assert {r["label"]: r["n"] for r in by_market["rows"]} == {
        "game_total": 2, "player_rushing_yards": 1}
    (row,) = api.get("/api/me/analytics", params={"group": "all"}).json()["selections"]["rows"]
    assert (row["label"], row["n"]) == ("All legs", 3)
    two = api.get("/api/me/analytics", params={
        "filter": ["market:game_total", "odds_band:−110 to +100"]}).json()["selections"]
    assert two["rows"][0]["n"] == 2 and two["caution"] is None
    three = api.get("/api/me/analytics", params={
        "filter": ["market:game_total", "odds_band:−110 to +100", "weekday:Sunday"]}).json()
    assert "chance" in three["selections"]["caution"]
    assert api.get("/api/me/analytics", params={"filter": "colour:red"}).status_code == 422
    assert api.get("/api/me/analytics", params={"group": "colour"}).status_code == 422


def test_a_group_at_the_minimum_sample_is_no_longer_low(api, db, monkeypatch):
    seed_analytics(db)
    monkeypatch.setenv("MIN_SAMPLE", "3")
    get_settings.cache_clear()
    body = api.get("/api/me/analytics").json()
    assert body["min_sample"] == 3 and body["selections"]["rows"][0]["low_sample"] is False


def test_tags_filter_any_and_all(api, db):
    seed_analytics(db)
    with Session(db) as s:
        wind = services.create_tag(s, "weather", "wind")
        cold = services.create_tag(s, "weather", "cold")
        legs = s.scalars(select(Leg).order_by(Leg.id)).all()
        legs[0].tags.extend([wind, cold])
        legs[1].tags.append(wind)
        s.commit()
        ids = [wind.id, cold.id]
    params = {"group": "all", "tag": ids}
    assert api.get("/api/me/analytics", params=params).json()["selections"]["rows"][0]["n"] == 2
    params["match"] = "all"
    assert api.get("/api/me/analytics", params=params).json()["selections"]["rows"][0]["n"] == 1


def test_the_money_view_gives_profit_roi_and_the_cumulative_series(api, db):
    seed_analytics(db)
    money = api.get("/api/me/analytics").json()["money"]
    (row,) = money["placed"]
    # staked 30, returned 19.09 + 0 + 22.00 = 41.09
    assert (row["label"], row["n"], row["staked"], row["returned"], row["profit"], row["roi"]) == (
        "single", 3, "30.00", "41.09", "11.09", "+37.0%")
    assert [p["profit"] for p in money["cumulative_profit"]] == ["9.09", "-0.91", "11.09"]
    assert money["unplaced"] == [] and money["unplaced_empty_text"].startswith("No unplaced")


def test_analytics_never_list_closing_prices(api, db):
    seed_analytics(db)
    text = api.get("/api/me/analytics").text
    assert "-120" not in text and "47.5" not in text and "closing_odds" not in text


# --- Settings -------------------------------------------------------------------------------------


def test_tags_add_rename_retire_and_merge(api):
    wind = api.post("/api/me/settings/tags", json={"category": "Weather", "name": "WR1 out"})
    assert wind.status_code == 201
    tag = wind.json()["tag"]
    assert (tag["category"], tag["name"], tag["label"]) == (
        "weather", "WR1 out", "weather: WR1 out")
    again = api.post("/api/me/settings/tags", json={"category": "weather", "name": "wr1 OUT"})
    assert again.status_code == 400 and "already exists" in again.json()["detail"]
    renamed = api.patch(f"/api/me/settings/tags/{tag['id']}", json={"name": "wind"}).json()["tag"]
    assert (renamed["category"], renamed["name"]) == ("weather", "wind")
    retired = api.patch(f"/api/me/settings/tags/{tag['id']}", json={"retired": True}).json()
    assert retired["tag"]["label"] == "weather: wind (retired)"
    body = api.get("/api/me/settings/tags").json()
    assert [t["id"] for t in body["tags"]] == [tag["id"]] and body["choices"] == []
    other = api.post("/api/me/settings/tags", json={"category": "weather", "name": "gusts"})
    merged = api.post(f"/api/me/settings/tags/{tag['id']}/merge",
                      json={"into": other.json()["tag"]["id"]})
    assert merged.json()["tag"]["name"] == "gusts"
    assert [t["name"] for t in api.get("/api/me/settings/tags").json()["tags"]] == ["gusts"]


def test_sportsbooks_add_and_update(api, db):
    added = api.post("/api/me/settings/sportsbooks",
                     json={"name": "Hard Rock", "odds_api_key": " hardrockbet "})
    assert added.status_code == 201 and added.json()["sportsbook"]["odds_api_key"] == "hardrockbet"
    book = added.json()["sportsbook"]
    clash = api.patch(f"/api/me/settings/sportsbooks/{book['id']}", json={"name": "draftkings"})
    assert clash.status_code == 400
    assert clash.json()["detail"] == "sportsbook draftkings already exists"
    names = [b["name"] for b in api.get("/api/me/settings/sportsbooks").json()["sportsbooks"]]
    assert names == ["BetMGM", "Caesars", "DraftKings", "FanDuel", "Hard Rock"]
    with Session(db) as s:
        assert s.scalars(select(Tag)).all() == []
