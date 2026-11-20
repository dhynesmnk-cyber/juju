"""The live chat's tests: a fake Odds API built from the recorded PIT @ CLE reply
(tests/fixtures/odds_api/nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json), and a clock a test
can move. Nothing here records anything new: the market list is made from the same reply.

The recorded game has 8 books (Hard Rock Bet among them), so the book of record is Hard Rock.
A few facts the tests lean on (DraftKings' prices; the others differ):
- DK Metcalf, receptions: main line 3.5; the alternate ladder has 1.5 to 8.5 (4.5 is +161).
- Harold Fannin Jr., receptions: main line 4.5.
- Spreads: Cleveland Browns +2.5, Pittsburgh Steelers -2.5. Game total 38.5.
- No book posts player_rush_tds, so that market is "not available".
"""
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from juju.ingest.odds_api import (
    ApiEvent, EventMarkets, EventOdds, OddsResponse, RequestRejected,
)
from tests.support import FIXTURES, load

ODDS_FILE = FIXTURES / "odds_api" / "nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json"
EVENT_ID = "d55cb69fed50a09170560b5b75d8de86"
ANOTHER_EVENT = "c9d8ed8aa4889486eaf10a630138ede0"
CHAIN = ["hardrockbet", "draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics",
         "betrivers", "espnbet", "ballybet", "betparx"]
START = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeLive:
    """What `LiveOdds` needs of the Odds API client. Every odds or markets call costs a credit
    per market (a markets call costs 1), and the count falls as The Odds API's would."""
    remaining: int | None = 20_000
    last_cost: int | None = None
    calls: list[tuple] = field(default_factory=list)
    fail: Exception | None = None
    reject: dict[str, RequestRejected] = field(default_factory=dict)
    hide_cost: bool = False   # a reply without the x-requests-last header
    drop: set[str] = field(default_factory=set)  # markets the books don't post for this game

    @property
    def quota_remaining(self) -> int | None:
        return self.remaining

    def _charge(self, cost: int) -> None:
        self.last_cost = None if self.hide_cost else cost
        if self.remaining is not None:
            self.remaining -= cost

    def events(self) -> list[ApiEvent]:
        self.calls.append(("events",))
        raw = load(ODDS_FILE)
        here = {k: raw[k] for k in ("id", "commence_time", "home_team", "away_team")}
        other = {"id": ANOTHER_EVENT, "commence_time": "2026-10-04T17:00:00Z",
                 "home_team": "Chicago Bears", "away_team": "Philadelphia Eagles"}
        return [ApiEvent.model_validate(e) for e in (here, other)]

    def event_odds(self, event_id, markets, bookmakers) -> OddsResponse:
        self.calls.append(("odds", event_id, tuple(markets)))
        if self.fail:
            raise self.fail
        if bad := next((self.reject[m] for m in markets if m in self.reject), None):
            raise bad
        raw = load(ODDS_FILE)
        raw["id"] = event_id
        raw["bookmakers"] = [b for b in raw["bookmakers"] if b["key"] in bookmakers]
        for b in raw["bookmakers"]:
            b["markets"] = [m for m in b["markets"]
                            if m["key"] in markets and m["key"] not in self.drop]
        raw["bookmakers"] = [b for b in raw["bookmakers"] if b["markets"]]
        cost = len(set(markets))
        self._charge(cost)
        return OddsResponse(EventOdds.model_validate(raw), json.dumps(raw), self.last_cost)

    def event_markets(self, event_id, bookmakers) -> EventMarkets:
        self.calls.append(("markets", event_id))
        if self.fail:
            raise self.fail
        raw = load(ODDS_FILE)
        books = [{"key": b["key"], "markets": [{"key": m["key"]} for m in b["markets"]
                                                if m["key"] not in self.drop]}
                 for b in raw["bookmakers"] if b["key"] in bookmakers]
        self._charge(1)
        return EventMarkets.model_validate({"id": event_id, "bookmakers": books})

    @property
    def paid_calls(self) -> list[tuple]:
        return [c for c in self.calls if c[0] != "events"]
