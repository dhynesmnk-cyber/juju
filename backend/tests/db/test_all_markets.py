"""Every market Juju captures, on a real response: PIT @ CLE, recorded 2026-09-30 about 21 hours
before kickoff with all 34 market keys and all 10 books (29 credits: 29 markets came back).

It checks what only a real response can:
- every catalog key was accepted (no 422);
- Hard Rock Bet is returned and leads the book chain;
- TD-scorer markets are "Yes" only (no line, no other side, so no fair value);
- Odds API player names match ESPN rosters.
"""
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from juju.api.views import player_view, team_view
from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.card import Outcome
from juju.core.enums import SnapshotSource
from juju.core.markets import ALL_KEYS
from juju.core.models import Game, PlayerNameMap
from juju.ingest import espn
from juju.ingest.odds_api import ApiEvent, EventOdds, OddsResponse
from juju.worker.archive import is_not_a_player, store_snapshot
from juju.worker.schedule import apply_scoreboard, upsert_odds_events, upsert_roster
from tests.support import EVENTS_FILE, FIXTURES, load

pytestmark = pytest.mark.db

ODDS = FIXTURES / "odds_api" / "nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json"
EVENT_ID = "d55cb69fed50a09170560b5b75d8de86"


@pytest.fixture
def game(db):
    raw = ODDS.read_text()
    doc = json.loads(raw)
    kickoff = EventOdds.model_validate(doc).commence_time
    with Session(db, expire_on_commit=False) as s:
        events = [ApiEvent.model_validate(e) for e in load(EVENTS_FILE) if e["id"] == EVENT_ID]
        upsert_odds_events(s, events, kickoff - timedelta(days=1))
        board = espn.parse_scoreboard(
            espn.Sport.NFL, load(FIXTURES / "espn" / "nfl_scoreboard_2026-10-01_scheduled.json"))
        apply_scoreboard(s, board.games)
        for team in ("23", "5"):
            roster = espn.parse_roster(load(FIXTURES / "espn" / f"nfl_roster_{team}.json"))
            upsert_roster(s, team, roster, kickoff - timedelta(days=1))
        g = s.scalars(select(Game).where(Game.odds_event_id == EVENT_ID)).one()
        # Stored as if captured 46 minutes before kickoff (the response itself is real).
        store_snapshot(s, g, OddsResponse(EventOdds.model_validate(doc), raw, 29),
                       source=SnapshotSource.LIVE, slot="t47",
                       fetched_at=kickoff - timedelta(minutes=46), markets=list(ALL_KEYS),
                       books=DEFAULT_BOOK_CHAIN)
        s.commit()
        return g


@pytest.mark.parametrize(("name", "expected"), [
    ("Cleveland Browns D/ST", True), ("Pittsburgh Steelers Defense", True),
    ("No Touchdown", True), ("Jaylen Warren", False), ("Myles Garrett", False),
])
def test_outcomes_that_are_not_players(name, expected):
    assert is_not_a_player(name) is expected


def test_every_key_was_accepted_and_hard_rock_is_there():
    doc = json.loads(ODDS.read_text())
    returned = {m["key"] for b in doc["bookmakers"] for m in b["markets"]}
    assert returned <= set(ALL_KEYS) and len(returned) == 29
    assert "hardrockbet" in {b["key"] for b in doc["bookmakers"]}


def test_player_names_match_the_rosters(db, game):
    with Session(db) as s:
        counts = dict(s.execute(select(PlayerNameMap.status, func.count())
                                .group_by(PlayerNameMap.status)).all())
    # All 31 real players match; team defenses and "No Touchdown" are recognised as not
    # players, so nothing is left for `cli unmapped`.
    assert counts == {"auto": 31}


def test_anytime_td_is_yes_only_and_led_by_hard_rock(db, game):
    with Session(db) as s:
        warren = s.scalar(select(PlayerNameMap.espn_athlete_id).where(
            PlayerNameMap.raw_name == "Jaylen Warren"))
        view = player_view(s, s.get(Game, game.id), warren,
                           game.commence_time - timedelta(minutes=10), DEFAULT_BOOK_CHAIN)
    cards = {c.key: c for c in view.cards}
    td = cards["player_anytime_td"]
    assert td.price["book"] == "hardrockbet" and td.price["timing"] == "on_time"
    assert td.line == 0.5 and td.fair_returns is None and td.fair_note
    assert td.outcome is Outcome.PREGAME and td.headline.endswith("if it hits")
    assert cards["player_1st_td"].price["book"] == "hardrockbet"
    rush = cards["player_rush_yds"]
    assert rush.fair_returns is not None  # two-sided: fair value shown


def test_a_market_hard_rock_lacks_falls_back_down_the_chain(db, game):
    with Session(db) as s:
        view = team_view(s, s.get(Game, game.id), "5",
                         game.commence_time - timedelta(minutes=10), DEFAULT_BOOK_CHAIN)
    cards = {c.key: c for c in view.cards}
    assert cards["h2h"].price["book"] == "hardrockbet"
    # No team totals at Hard Rock or DraftKings for this game: FanDuel's price is shown.
    assert cards["team_totals"].price["book"] == "fanduel"
