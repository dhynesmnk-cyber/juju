"""The tracker's view of ESPN and The Odds API, over Juju's shared router and client.

After parlaytracker@c3bd43c: its `ingest/espn.py` box-score columns, its `ingest/guards.py`
bounds, and the calls its worker makes (`scoreboard(sport, day)`, `box_score(sport, event)`,
`events(sport)`, `event_odds(sport, event, markets)`). The requests themselves go through Juju's
router and client, so there is one rate limit, one set of breakers and one Odds API quota.

Box scores are read by Juju's parser with the tracker's column table: its eight NFL markets use
exactly the columns of the matching `Stat` in Juju's `espn.STAT_COLUMNS` (one definition, not
two), plus NBA points and NHL goals + assists. A player missing from every table has no value
(neither zero nor void), as parlaytracker settles; Juju's `appeared` is not used here.
"""
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from juju.core.enums import DataSource, Sport, Stat
from juju.ingest import espn
from juju.ingest.guards import ImplausibleError
from juju.ingest.odds_api import ApiEvent, EventOdds, OddsApiClient
from juju.ingest.router import Breakers, EspnRouter, Routed
from juju.tracker.models import MarketType
from juju.tracker.resolve import SPORT_KEYS

# The NFL markets, by the Juju stat that reads the same columns.
NFL_STATS: dict[MarketType, Stat] = {
    MarketType.PLAYER_RECEPTIONS: Stat.RECEPTIONS,
    MarketType.PLAYER_RECEIVING_YARDS: Stat.RECEIVING_YARDS,
    MarketType.PLAYER_RUSHING_YARDS: Stat.RUSHING_YARDS,
    MarketType.PLAYER_PASSING_YARDS: Stat.PASSING_YARDS,
    MarketType.PLAYER_PASS_COMPLETIONS: Stat.PASS_COMPLETIONS,
    MarketType.PLAYER_TOUCHDOWNS: Stat.TOUCHDOWNS,
    MarketType.PLAYER_INTERCEPTIONS: Stat.INTERCEPTIONS_THROWN,
    MarketType.PLAYER_FIELD_GOALS: Stat.FIELD_GOALS,
}

# Per sport: the box-score groups and columns each player market adds up. Keys, never
# positions or labels. NBA's group has no name; NHL has no points column: goals + assists.
MARKET_COLUMNS: dict[Sport, dict[MarketType, tuple[espn.Source, ...]]] = {
    Sport.NFL: {market: espn.STAT_COLUMNS[stat] for market, stat in NFL_STATS.items()},
    Sport.NBA: {MarketType.PLAYER_POINTS: ((("",), (("points", 0, "sum"),)),)},
    Sport.NHL: {MarketType.PLAYER_POINTS: ((("forwards", "defenses"),
                                            (("goals", 0, "sum"), ("assists", 0, "sum"))),)},
    Sport.MLB: {},
}

# Plausibility bounds (parlaytracker's guards; points have none in its spec).
MARKET_BOUNDS: dict[MarketType, tuple[int, int]] = {
    MarketType.PLAYER_RECEIVING_YARDS: (-30, 400),
    MarketType.PLAYER_RUSHING_YARDS: (-30, 400),
    MarketType.PLAYER_PASSING_YARDS: (-30, 700),
    MarketType.PLAYER_RECEPTIONS: (0, 25),
    MarketType.PLAYER_PASS_COMPLETIONS: (0, 70),
    MarketType.PLAYER_TOUCHDOWNS: (0, 8),
    MarketType.PLAYER_INTERCEPTIONS: (0, 10),
    MarketType.PLAYER_FIELD_GOALS: (0, 10),
}


def check_market_stats(stats: dict[MarketType, dict[str, Decimal]]) -> None:
    """Reject the whole box score if any value is outside its bounds."""
    for market, by_athlete in stats.items():
        bounds = MARKET_BOUNDS.get(market)
        if bounds is None:
            continue
        for athlete, value in by_athlete.items():
            if not bounds[0] <= value <= bounds[1]:
                raise ImplausibleError(
                    f"{market} {value} for athlete {athlete} is outside {bounds}")


def parse_box_score(sport: Sport, payload) -> espn.BoxScore:
    """A box score's scores, and its stats keyed by the sport's markets."""
    return espn.parse_box_score(payload, sport, MARKET_COLUMNS[sport])


class EspnFeed:
    """ESPN as the tracker's jobs ask for it, through Juju's router."""

    def __init__(self, router: EspnRouter):
        self._router = router

    @property
    def breakers(self) -> Breakers:
        return self._router.breakers

    def scoreboard(self, sport: Sport, day: date, max_wait: float = 0.0, *,
                   only: DataSource | None = None) -> Routed[espn.ScoreboardResult]:
        return self._router.scoreboard(day, max_wait, only=only, sport=sport)

    def box_score(self, sport: Sport, espn_event_id: str, max_wait: float = 0.0, *,
                  only: DataSource | None = None) -> Routed[espn.BoxScore]:
        return self._router.box_score(espn_event_id, max_wait, sport=sport,
                                      columns=MARKET_COLUMNS[sport],
                                      check_stats=check_market_stats, only=only)


class OddsFeed:
    """The Odds API as the tracker's closing-line capture asks for it, through Juju's client:
    one quota, and Juju's book chain (Hard Rock first, at most 10 books, the cost of one
    region) rather than parlaytracker's `regions=us`, which never returned Hard Rock."""

    def __init__(self, client: OddsApiClient, books: Sequence[str]):
        self._client = client
        self._books = list(books)

    @property
    def quota_remaining(self) -> int | None:
        return self._client.quota_remaining

    def events(self, sport: Sport) -> list[ApiEvent]:
        return self._client.events(sport_key=SPORT_KEYS[sport])

    def event_odds(self, sport: Sport, event_id: str, markets: Sequence[str]) -> EventOdds:
        return self._client.event_odds(event_id, markets, self._books,
                                       sport_key=SPORT_KEYS[sport]).odds
