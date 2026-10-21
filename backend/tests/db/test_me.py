"""The owners' own section (/api/me): saving the bet pages they open, their game recaps and
history, on the recorded PHI @ CHI game. Only an owner's own lookups, behind their own sign-in
(docs/licensing.md)."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.api import me as me_module
from juju.config import get_settings
from juju.core.enums import EventStatus
from juju.core.models import Game
from juju.tracker.models import SavedLookup
from tests.db.test_api import client, seed  # noqa: F401 (fixtures)
from tests.db.test_parlay import keys_in
from tests.support import BARKLEY, HURTS, SMITH

pytestmark = pytest.mark.db

CHRIS = {"x-juju-user": "chris"}
ASH = {"x-juju-user": "ash"}
SMITH_CATCH = 6  # "D. Smith 30-yd catch", thrown by Hurts (the recorded game's sixth play)
LEGS = f"t-3-spreads,p-{BARKLEY}-player_rush_yds"


@pytest.fixture
def me(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("TRACKER_USERS", "chris:passcode-chris-1,ash:passcode-ash-22")
    get_settings.cache_clear()
    return client


def save(api, game, who=CHRIS, **body):
    r = api.post("/api/me/lookups", json={"game_id": game, **body}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def rows(db) -> list[SavedLookup]:
    with Session(db) as s:
        return list(s.scalars(select(SavedLookup).order_by(SavedLookup.id)))


def later_game(db, kickoff: datetime) -> int:
    with Session(db) as s:
        g = Game(home_team="Cleveland Browns", away_team="Pittsburgh Steelers", home_abbr="CLE",
                 away_abbr="PIT", home_espn_id="5", away_espn_id="23", commence_time=kickoff,
                 status=EventStatus.SCHEDULED)
        s.add(g)
        s.commit()
        return g.id


def test_off_without_owners(client, db):  # noqa: F811
    game = seed(db, "final")
    assert client.get("/api/me/recaps", headers=CHRIS).status_code == 404
    r = client.post("/api/me/lookups", json={"kind": "player", "game_id": game,
                                             "target": BARKLEY}, headers=CHRIS)
    assert r.status_code == 404


def test_only_a_known_owner(me, db):
    seed(db, "final")
    assert me.get("/api/me/recaps").status_code == 401
    assert me.get("/api/me/recaps", headers={"x-juju-user": "bob"}).status_code == 401
    r = me.get("/api/me/recaps", headers=CHRIS)
    assert r.status_code == 200 and r.json() == {"games": []}


def test_every_kind_of_bet_page_is_saved(me, db):
    game = seed(db, "final")
    assert save(me, game, kind="player", target=BARKLEY, market="player_rush_yds")["new"]
    save(me, game, kind="team", target="3", market="spreads")
    save(me, game, kind="parlay", target=LEGS)
    games = me.get("/api/me/recaps", headers=CHRIS).json()["games"]
    assert [(g["game"]["label"], g["lookups"], g["state"]) for g in games] == [
        ("PHI @ CHI", 3, "final")]


def test_the_same_page_twice_is_one_lookup(me, db):
    game = seed(db, "final")
    first = save(me, game, kind="player", target=SMITH, play="catch", yards=30)
    again = save(me, game, kind="player", target=SMITH, play="catch", yards=45)
    assert again == {"id": first["id"], "new": False}
    save(me, game, kind="parlay", target=LEGS)
    reordered = f"p-{BARKLEY}-player_rush_yds,t-3-spreads"
    assert not save(me, game, kind="parlay", target=reordered)["new"]
    save(me, game, kind="player", target=BARKLEY, market="player_rush_yds", threshold="79.5")
    save(me, game, kind="player", target=BARKLEY, market="player_rush_yds", threshold="99.5")
    saved = rows(db)
    assert len(saved) == 4  # Smith's catch, the parlay, and two thresholds
    assert (saved[0].times, saved[0].yards) == (2, 45)  # the latest visit wins
    assert saved[1].target.startswith(f"p-{BARKLEY}")  # the link back: as last opened


def test_a_tapped_play_counts_as_what_it_was(me, db):
    game = seed(db, "final")
    save(me, game, kind="player", target=SMITH, play_id=SMITH_CATCH)
    save(me, game, kind="player", target=HURTS, play_id=SMITH_CATCH)  # he threw it
    save(me, game, kind="player", target=BARKLEY, play_id=SMITH_CATCH)  # not his: ignored
    smith, hurts, barkley = rows(db)
    assert (smith.play, smith.play_id) == ("catch", SMITH_CATCH)
    assert (hurts.play, hurts.play_id) == ("pass", SMITH_CATCH)
    assert (barkley.play, barkley.play_id) == (None, None)
    # Typed "Smith catch" later is the same lookup, and keeps the play it knew.
    assert not save(me, game, kind="player", target=SMITH, play="catch")["new"]
    assert rows(db)[0].play_id == SMITH_CATCH


def test_what_isnt_in_the_game_is_refused(me, db):
    game = seed(db, "final")
    post = lambda body: me.post("/api/me/lookups", json=body, headers=CHRIS)  # noqa: E731
    assert post({"kind": "player", "game_id": 999, "target": BARKLEY}).status_code == 404
    assert post({"kind": "player", "game_id": game, "target": "1"}).status_code == 404
    assert post({"kind": "team", "game_id": game, "target": "5"}).status_code == 404
    assert post({"kind": "parlay", "game_id": game, "target": "p-1-x"}).status_code == 422
    assert post({"kind": "parlay", "game_id": game,
                 "target": "t-5-spreads,t-3-totals"}).status_code == 404
    assert post({"kind": "slip", "game_id": game, "target": "3"}).status_code == 422
    assert rows(db) == []


def test_a_recap_works_every_bet_out_as_its_page_does(me, db):
    game = seed(db, "final")
    save(me, game, kind="player", target=BARKLEY, market="player_rush_yds")
    save(me, game, kind="player", target=BARKLEY, market="player_rush_yds", threshold="79.5")
    save(me, game, kind="team", target="3", market="spreads")
    save(me, game, kind="parlay", target=LEGS)
    r = me.get(f"/api/me/recaps/{game}", headers=CHRIS)
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["state"] == "final" and body["game"]["label"] == "PHI @ CHI"
    rush, ladder, spread, sgp = body["lookups"]
    assert (rush["who"], rush["asked"]) == ("Saquon Barkley", "Rush yards")
    assert [(b["bet"], b["outcome"], b["headline"], b["back"]) for b in rush["bets"]] == [
        ("Over 73.5 rush yards", "won", "$10 → $18.93", "18.93")]  # 82 rush yards
    assert rush["bets"][0]["price"] == {"american": -112, "book_name": "DraftKings",
                                        "minutes_before_kickoff": 126, "timing": "early"}
    assert ladder["asked"] == "80+ rush yards"
    assert [b["bet"] for b in ladder["bets"]] == ["Over 73.5 rush yards", "80+ rush yards"]
    assert (spread["who"], spread["bets"][0]["outcome"]) == ("Chicago Bears", "won")
    assert sgp["parlay"]["outcome"] == "won" and sgp["parlay"]["book_name"] == "DraftKings"
    assert sgp["parlay"]["back"] == sgp["parlay"]["headline"].removeprefix("$10 → $")
    # Barkley's 73.5 shows in two lookups and counts once: rush, 80+, spread, parlay.
    totals = body["totals"]
    assert (totals["bets"], totals["won"], totals["lost"], totals["open"]) == (4, 4, 0, 0)
    assert totals["staked"] == "40.00"
    assert totals["back"] == f"{18.93 + 21.20 + 18.70 + float(sgp['parlay']['back']):.2f}"
    # The price its page shows, and no other book's (docs/licensing.md).
    assert not keys_in(body) & {"others", "provenance", "observed_at", "fair_returns"}


def test_a_plain_look_shows_the_page_s_first_bets(me, db):
    game = seed(db, "final")
    save(me, game, kind="player", target=SMITH)
    (entry,) = me.get(f"/api/me/recaps/{game}", headers=CHRIS).json()["lookups"]
    assert entry["asked"] is None and 1 <= len(entry["bets"]) <= 3
    assert all(b["price"] is not None for b in entry["bets"])


def test_owners_see_only_their_own(me, db):
    game = seed(db, "final")
    lookup = save(me, game, kind="player", target=BARKLEY)
    assert me.get(f"/api/me/recaps/{game}", headers=ASH).status_code == 404
    assert me.get("/api/me/recaps", headers=ASH).json() == {"games": []}
    assert me.delete(f"/api/me/lookups/{lookup['id']}", headers=ASH).status_code == 404
    r = me.delete(f"/api/me/lookups/{lookup['id']}", headers=CHRIS)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert me.get(f"/api/me/recaps/{game}", headers=CHRIS).status_code == 404
    assert rows(db) == []


def test_history_lists_earlier_games_only(me, db, monkeypatch):
    game = seed(db, "final")  # kickoff 2026-09-29 00:15 UTC
    earlier = later_game(db, datetime(2026, 9, 21, 17, tzinfo=UTC))
    nxt = later_game(db, datetime(2026, 10, 5, 17, tzinfo=UTC))
    save(me, game, kind="player", target=BARKLEY, market="player_rush_yds")
    save(me, earlier, kind="player", target=BARKLEY)
    save(me, game, ASH, kind="player", target=BARKLEY)
    r = me.get(f"/api/me/history/player/{BARKLEY}", params={"before_game": nxt}, headers=CHRIS)
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["name"] == "Saquon Barkley"
    assert [g["game"]["id"] for g in body["games"]] == [game, earlier]  # latest first
    assert [b["bet"] for b in body["games"][0]["bets"]] == ["Over 73.5 rush yards"]
    assert body["games"][1]["bets"] == []  # no prices on file for that one
    assert (body["totals"]["bets"], body["totals"]["won"]) == (1, 1)
    # Opened from the recorded game itself: only what came before it.
    body = me.get(f"/api/me/history/player/{BARKLEY}", params={"before_game": game},
                  headers=CHRIS).json()
    assert [g["game"]["id"] for g in body["games"]] == [earlier]
    monkeypatch.setattr(me_module, "HISTORY_GAMES", 1)
    body = me.get(f"/api/me/history/player/{BARKLEY}", params={"before_game": nxt},
                  headers=CHRIS).json()
    assert [g["game"]["id"] for g in body["games"]] == [game]
    assert me.get(f"/api/me/history/player/{BARKLEY}", params={"before_game": 999},
                  headers=CHRIS).status_code == 404


def test_the_home_strip_lists_games_just_finished(me, db, monkeypatch):
    game = seed(db, "final")  # final at 03:25 UTC on 2026-09-29
    save(me, game, kind="player", target=BARKLEY, market="player_rush_yds")
    monkeypatch.setattr(me_module, "_utcnow", lambda: datetime(2026, 9, 29, 12, tzinfo=UTC))
    (item,) = me.get("/api/me/recaps", params={"recent": 1}, headers=CHRIS).json()["games"]
    assert item["lookups"] == 1 and item["totals"]["won"] == 1
    monkeypatch.setattr(me_module, "_utcnow", lambda: datetime(2026, 10, 2, tzinfo=UTC))
    assert me.get("/api/me/recaps", params={"recent": 1}, headers=CHRIS).json() == {"games": []}
    assert len(me.get("/api/me/recaps", headers=CHRIS).json()["games"]) == 1
