"""Shared test helpers: recorded fixtures, a seeded game, and a fake Odds API.

The recorded game is PHI @ CHI on 2026-09-28 (Monday night): its Odds API odds (taken about
two hours before kickoff), its ESPN summary, scoreboard and both rosters.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from juju.core.models import Game
from juju.ingest import espn
from juju.ingest.odds_api import (
    ApiEvent, ApiGameScore, EventOdds, OddsResponse, RequestRejected,
)
from juju.worker.schedule import apply_scoreboard, upsert_odds_events, upsert_roster

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ODDS_FILE = FIXTURES / "odds_api" / "nfl_event_odds_2026-09-28_PHI-CHI.json"
EVENTS_FILE = FIXTURES / "odds_api" / "nfl_events_2026-09-28.json"
SUMMARY_FILE = FIXTURES / "espn" / "nfl_summary_401872963_full.json"
PHI_CHI_ODDS_ID = "47dc7baa254659f3beb2ed2b38c207b6"
PHI_CHI_ESPN_ID = "401872963"
KICKOFF = datetime.fromisoformat("2026-09-29T00:15:00+00:00")
BARKLEY = "3929630"
HURTS = "4040715"
SMITH = "4241478"  # DeVonta Smith


def load(path: Path):
    return json.loads(path.read_text())


NFLVERSE_FILES = {"schedules": "games.csv", "players": "players.csv",
                  "player_stats": "stats_player_week_{season}.csv",
                  "pbp": "play_by_play_{season}.csv"}


def nflverse_loader(replace: dict[str, bytes] | None = None, calls: list | None = None):
    """Serves the recorded nflverse slice (PHI @ CHI final, PIT @ CLE not played yet) as the
    real loader would return it; `replace` swaps a dataset's bytes."""
    def loader(dataset: str, season: int) -> bytes:
        if calls is not None:
            calls.append(dataset)
        if replace and dataset in replace:
            return replace[dataset]
        name = NFLVERSE_FILES[dataset].format(season=season)
        return (FIXTURES / "nflverse" / name).read_bytes()
    return loader


def seed_phi_chi(session: Session, now: datetime) -> Game:
    """The game, known to both The Odds API and ESPN, with both rosters."""
    events = [ApiEvent.model_validate(e) for e in load(EVENTS_FILE)]
    upsert_odds_events(session, events[:1], now)
    board = espn.parse_scoreboard(espn.Sport.NFL, load(
        FIXTURES / "espn" / "nfl_scoreboard_2026-09-28_final.json"))
    apply_scoreboard(session, board.games)
    for team in ("21", "3"):
        roster = espn.parse_roster(load(FIXTURES / "espn" / f"nfl_roster_{team}.json"))
        upsert_roster(session, team, roster, now)
    session.commit()
    return session.query(Game).filter_by(odds_event_id=PHI_CHI_ODDS_ID).one()


@dataclass
class FakeOdds:
    """Serves the recorded odds for any event; can reject market keys like the real API."""
    quota_remaining: int | None = 90_000
    reject: set[str] = field(default_factory=set)
    fail: bool = False
    calls: list[tuple] = field(default_factory=list)
    vendor_ts: datetime | None = None

    def _odds(self, markets) -> OddsResponse:
        if self.fail:
            from juju.core.enums import FailureKind
            from juju.ingest.http import FetchError
            raise FetchError("https://api.the-odds-api.com", FailureKind.TRANSIENT, "timeout")
        if bad := self.reject & set(markets):
            raise RequestRejected(f"HTTP 422: unknown market {sorted(bad)}", 422)
        raw = load(ODDS_FILE)
        for b in raw["bookmakers"]:
            b["markets"] = [m for m in b["markets"] if m["key"] in markets]
        text = json.dumps(raw)
        cost = len({m["key"] for b in raw["bookmakers"] for m in b["markets"]})
        if self.quota_remaining is not None:
            self.quota_remaining -= cost
        return OddsResponse(EventOdds.model_validate(raw), text, cost, self.vendor_ts)

    def events(self):
        return [ApiEvent.model_validate(e) for e in load(EVENTS_FILE)]

    def event_odds(self, event_id, markets, bookmakers):
        self.calls.append(("live", event_id, tuple(markets)))
        return self._odds(markets)

    def historical_event_odds(self, event_id, at, markets, bookmakers):
        self.calls.append(("historical", event_id, tuple(markets), at))
        return self._odds(markets)

    def historical_events(self, at):
        return self.events()

    def scores(self, days_from=None) -> list[ApiGameScore]:
        return []
