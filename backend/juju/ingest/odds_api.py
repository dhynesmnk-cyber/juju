"""The Odds API client and parsers.

After parlaytracker@c3bd43c parlaytracker/ingest/odds_api.py. Added: the `bookmakers`
parameter, market-level `last_update` (kept for provenance), the historical endpoints (gap repair
and backfill), `/scores`, and the raw body of every odds response (hashed and stored with the
snapshot).

Every call goes through the shared HTTP layer and the `odds_api` circuit breaker. The API key is
a query parameter, so nothing here may log a URL with its parameters.
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, TypeAdapter, ValidationError

from juju.core.enums import FailureKind
from juju.ingest.http import FetchError, RateLimited, RateLimiter, get_json_response
from juju.ingest.router import Breakers

log = logging.getLogger("juju.odds_api")

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
SOURCE = "odds_api"


class ApiEvent(BaseModel):
    id: str
    commence_time: datetime
    home_team: str
    away_team: str


class ApiOutcome(BaseModel):
    name: str  # "Over" / "Under" / "Yes" / "No", or a team name
    description: str | None = None  # the player, or the team for team totals
    price: int  # American, because we ask for oddsFormat=american
    point: float | None = None


class ApiMarket(BaseModel):
    key: str
    last_update: datetime | None = None
    outcomes: list[ApiOutcome]


class ApiBookmaker(BaseModel):
    key: str
    markets: list[ApiMarket] = []


class EventOdds(BaseModel):
    id: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[ApiBookmaker] = []


class ApiScore(BaseModel):
    name: str
    score: str


class ApiGameScore(BaseModel):
    id: str
    commence_time: datetime
    completed: bool = False
    home_team: str
    away_team: str
    scores: list[ApiScore] | None = None
    last_update: datetime | None = None


class _HistoricalOdds(BaseModel):
    timestamp: datetime
    data: EventOdds


class _HistoricalEvents(BaseModel):
    timestamp: datetime
    data: list[ApiEvent]


_EVENTS = TypeAdapter(list[ApiEvent])
_EVENT_ODDS = TypeAdapter(EventOdds)
_SCORES = TypeAdapter(list[ApiGameScore])
_HIST_ODDS = TypeAdapter(_HistoricalOdds)
_HIST_EVENTS = TypeAdapter(_HistoricalEvents)


class RequestRejected(Exception):
    """The API refused this request (for example an unknown market key): not the provider's
    fault, so it doesn't count against the breaker."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OddsResponse:
    odds: EventOdds
    raw: str                 # the body exactly as received, for the snapshot's provenance
    cost: int | None         # x-requests-last
    vendor_ts: datetime | None = None  # historical snapshots only


class OddsSource(Protocol):
    """What the capture and repair jobs need; tests supply a fake."""

    quota_remaining: int | None

    def events(self) -> list[ApiEvent]: ...

    def event_odds(self, event_id: str, markets: Sequence[str],
                   bookmakers: Sequence[str]) -> OddsResponse: ...

    def historical_event_odds(self, event_id: str, at: datetime, markets: Sequence[str],
                              bookmakers: Sequence[str]) -> OddsResponse: ...

    def historical_events(self, at: datetime) -> list[ApiEvent]: ...

    def scores(self, days_from: int | None = None) -> list[ApiGameScore]: ...


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class OddsApiClient:
    def __init__(self, api_key: str, breakers: Breakers, limiter: RateLimiter | None = None):
        self._api_key = api_key
        self._breakers = breakers
        # The worker is the only caller: a generous limit that never delays a capture.
        self._limiter = limiter or RateLimiter(per_host_interval=0.5, per_minute=60)
        self.quota_remaining: int | None = breakers[SOURCE].quota_remaining
        self.last_cost: int | None = None
        self.calls = 0
        self._raw = ""

    def events(self) -> list[ApiEvent]:
        """Upcoming games. Costs 0 credits."""
        return self._parse(_EVENTS, self._get(f"/sports/{SPORT}/events", {}))

    def event_odds(self, event_id: str, markets: Sequence[str],
                   bookmakers: Sequence[str]) -> OddsResponse:
        """One game's odds. Costs (markets returned) x (1 per 10 books) credits."""
        data = self._get(f"/sports/{SPORT}/events/{event_id}/odds",
                         self._odds_params(markets, bookmakers))
        return OddsResponse(self._parse(_EVENT_ODDS, data), self._raw, self.last_cost)

    def historical_event_odds(self, event_id: str, at: datetime, markets: Sequence[str],
                              bookmakers: Sequence[str]) -> OddsResponse:
        """The vendor's snapshot closest to, and not after, `at`. Costs 10x a live call."""
        params = {**self._odds_params(markets, bookmakers), "date": _iso(at)}
        data = self._get(f"/historical/sports/{SPORT}/events/{event_id}/odds", params)
        hist = self._parse(_HIST_ODDS, data)
        return OddsResponse(hist.data, self._raw, self.last_cost, hist.timestamp)

    def historical_events(self, at: datetime) -> list[ApiEvent]:
        """The games listed at `at`. Costs 1 credit (0 when there are none)."""
        data = self._get(f"/historical/sports/{SPORT}/events", {"date": _iso(at)})
        return self._parse(_HIST_EVENTS, data).data

    def scores(self, days_from: int | None = None) -> list[ApiGameScore]:
        """Live and recent scores. Costs 1 credit, 2 with `days_from`."""
        params = {"dateFormat": "iso"}
        if days_from:
            params["daysFrom"] = str(days_from)
        return self._parse(_SCORES, self._get(f"/sports/{SPORT}/scores", params))

    # --- internals ------------------------------------------------------------------------

    @staticmethod
    def _odds_params(markets: Sequence[str], bookmakers: Sequence[str]) -> dict[str, str]:
        return {"oddsFormat": "american", "dateFormat": "iso", "markets": ",".join(markets),
                "bookmakers": ",".join(bookmakers)}

    def _get(self, path: str, params: dict[str, str]) -> Any:
        self._breakers.allow(SOURCE)  # raises ProviderOpen
        url = BASE_URL + path
        self.calls += 1
        try:
            response = get_json_response(url, {**params, "apiKey": self._api_key}, self._limiter)
        except RateLimited:
            raise
        except FetchError as e:
            if e.status_code in (401, 422):
                # A rejected request (unknown market, bad key, plan without history) isn't an
                # outage: report it without opening the breaker.
                raise RequestRejected(f"HTTP {e.status_code} for {path}: "
                                      f"{(e.body or '')[:200]}", e.status_code) from e
            self._breakers.failure(SOURCE, e.kind, e.detail, e.retry_after)
            raise
        self._read_quota(response.headers)
        self._raw = response.text
        return response.data

    def _read_quota(self, headers) -> None:
        for name, attr in (("x-requests-remaining", "quota_remaining"),
                           ("x-requests-last", "last_cost")):
            try:
                setattr(self, attr, int(float(headers[name])))
            except (KeyError, ValueError):
                pass
        self._breakers.success(SOURCE, self.quota_remaining)

    def _parse(self, adapter: TypeAdapter, data):
        try:
            return adapter.validate_python(data)
        except ValidationError as e:
            # A 200 that doesn't fit the model: retrying won't fix a format change.
            self._breakers.failure(SOURCE, FailureKind.SCHEMA, f"{e.error_count()} errors")
            raise FetchError(BASE_URL, FailureKind.SCHEMA, "response does not match the model",
                             body=self._raw[:1_000_000]) from e
