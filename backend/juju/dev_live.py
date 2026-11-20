"""Development and end-to-end tests only: the live chat's Odds API, played from the recordings.

With `LIVE_ODDS_FIXTURE=1` the API answers the live chat from the recorded odds in
`tests/fixtures/odds_api/` instead of calling The Odds API, so the chat can be run, and
Playwright can drive it, with no key and no credits (web/README.md). Like `dev_seed.py`, it
reads fixtures from `tests/`, which the production image doesn't ship. The credits count down
from 20,000 in memory as a real key's would, one per market asked for.

Two games: PHI @ CHI, the seeded game (`python -m juju.cli seed live`, in play, so "where he
stands" reads the box score), and PIT @ CLE (every market, with Hard Rock Bet among the books).
"""
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from juju.ingest.odds_api import ApiEvent, EventMarkets, EventOdds, OddsResponse

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "odds_api"
_FILES = {
    "47dc7baa254659f3beb2ed2b38c207b6": "nfl_event_odds_2026-09-28_PHI-CHI.json",
    "d55cb69fed50a09170560b5b75d8de86": "nfl_event_odds_2026-10-01_PIT-CLE_all_markets.json",
}
_STARTS = {  # relative to now: PHI @ CHI is in play, PIT @ CLE is days away
    "47dc7baa254659f3beb2ed2b38c207b6": timedelta(hours=-1),
    "d55cb69fed50a09170560b5b75d8de86": timedelta(days=2),
}


def _load(event_id: str) -> dict:
    return json.loads((FIXTURES / _FILES[event_id]).read_text())


class FixtureLive:
    """What `ingest/live_odds.LiveSource` needs of the Odds API client."""

    def __init__(self, remaining: int = 20_000):
        self._remaining = remaining
        self.last_cost: int | None = None
        self._lock = threading.Lock()

    @property
    def quota_remaining(self) -> int | None:
        return self._remaining

    def events(self) -> list[ApiEvent]:
        now = datetime.now(tz=UTC)
        out = []
        for event_id in _FILES:
            raw = _load(event_id)
            start = (now + _STARTS[event_id]).replace(microsecond=0)
            out.append(ApiEvent.model_validate({
                "id": event_id, "commence_time": start.isoformat(),
                "home_team": raw["home_team"], "away_team": raw["away_team"]}))
        return sorted(out, key=lambda e: e.commence_time)

    def _charge(self, cost: int) -> None:
        with self._lock:
            self.last_cost = cost
            self._remaining -= cost

    def event_odds(self, event_id: str, markets, bookmakers) -> OddsResponse:
        raw = _load(event_id)
        raw["bookmakers"] = [b for b in raw["bookmakers"] if b["key"] in bookmakers]
        for b in raw["bookmakers"]:
            b["markets"] = [m for m in b["markets"] if m["key"] in markets]
        raw["bookmakers"] = [b for b in raw["bookmakers"] if b["markets"]]
        self._charge(len(set(markets)))
        return OddsResponse(EventOdds.model_validate(raw), json.dumps(raw), self.last_cost)

    def event_markets(self, event_id: str, bookmakers) -> EventMarkets:
        raw = _load(event_id)
        books = [{"key": b["key"], "markets": [{"key": m["key"]} for m in b["markets"]]}
                 for b in raw["bookmakers"] if b["key"] in bookmakers]
        self._charge(1)
        return EventMarkets.model_validate({"id": event_id, "bookmakers": books})
