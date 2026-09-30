"""A result card's outcome and money (docs/GOALS.md section 5). Pure: no database access.

A card has two independent parts: the price (`core/t45.py`) and the outcome, decided here from
the game's state and the player's stat. Settling needs the game to have been final for 10
minutes (parlaytracker's rule). Before that, a line already beaten is LOCKED: paid if the play
stands.
"""
import enum
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from juju.core import odds
from juju.core.enums import EventStatus, LegResult
from juju.core.live import Severity, freshness
from juju.core.markets import Market, Scope
from juju.core.settlement import settle_moneyline, settle_over, settle_spread

STAKE = Decimal("10.00")
SETTLE_AFTER = timedelta(minutes=10)


class Outcome(enum.StrEnum):
    PREGAME = "pregame"
    WAITING_FOR_FEED = "waiting_for_feed"
    LIVE = "live"
    LOCKED = "locked"
    GONE = "gone"
    WON = "won"
    LOST = "lost"
    PUSH = "push"
    VOID = "void"
    NO_STAT_LINE = "no_stat_line"
    UNTRACKED = "untracked"
    UNAVAILABLE = "unavailable"


UNKNOWN_SCORER = ""  # first_td_scorer when a touchdown happened but its scorer isn't matched

SETTLED = frozenset({Outcome.WON, Outcome.LOST, Outcome.PUSH, Outcome.VOID,
                     Outcome.NO_STAT_LINE})


@dataclass(frozen=True)
class GameState:
    status: EventStatus
    commence_time: datetime
    home_score: int | None
    away_score: int | None
    final_at: datetime | None
    last_polled_at: datetime | None


@dataclass(frozen=True)
class Bet:
    """What the card prices: a market, its line, and (for game markets) the side."""
    market: Market
    line: Decimal | None       # None for a moneyline
    side_is_home: bool | None = None  # spreads, moneylines and team totals


@dataclass(frozen=True)
class PlayerStat:
    value: Decimal | None      # the stat so far; None when the player has no stat line
    first_td_scorer: str | None = None   # ESPN id of the game's first TD scorer, if any
    athlete_id: str | None = None


@dataclass(frozen=True)
class Decided:
    outcome: Outcome
    current: Decimal | None = None   # the value compared with the line, if known
    needed: Decimal | None = None    # how much more an Over needs, while live


def is_settled_time(game: GameState, now: datetime) -> bool:
    return (game.status is EventStatus.FINAL and game.final_at is not None
            and now >= game.final_at + SETTLE_AFTER)


def _game_value(bet: Bet, game: GameState) -> Decimal | None:
    if game.home_score is None or game.away_score is None:
        return None
    home, away = Decimal(game.home_score), Decimal(game.away_score)
    own, other = (home, away) if bet.side_is_home else (away, home)
    match bet.market.scope:
        case Scope.TOTAL:
            return home + away
        case Scope.TEAM:
            return own
        case _:  # spread and moneyline compare the margin
            return own - other


def decide(bet: Bet, game: GameState, stat: PlayerStat | None, now: datetime) -> Decided:
    """The outcome of the bet right now."""
    if game.status in (EventStatus.POSTPONED, EventStatus.CANCELLED):
        return Decided(Outcome.VOID)
    if game.status is EventStatus.SCHEDULED or now < game.commence_time:
        return Decided(Outcome.PREGAME)
    settled = is_settled_time(game, now)
    # No fresh reading of a game in play (parlaytracker's red: 5 minutes, 7 at a break) is
    # "live status unavailable", never a guess.
    stale = freshness(game.status, game.last_polled_at, now) is Severity.RED
    market = bet.market
    if bet.line is None and market.scope is not Scope.MONEYLINE and not market.first_td:
        # No price, so no line to decide it on (the card says "no price on file").
        return Decided(Outcome.UNTRACKED)

    if market.scope is not Scope.PLAYER:
        value = _game_value(bet, game)
        if value is None or stale:
            return Decided(Outcome.UNAVAILABLE)
        if market.scope is Scope.MONEYLINE:
            if settled:
                return Decided(_result(settle_moneyline(value)), value)
            return Decided(Outcome.LIVE, value)
        assert bet.line is not None
        if market.scope is Scope.SPREAD:
            if settled:
                return Decided(_result(settle_spread(bet.line, value)), value)
            return Decided(Outcome.LIVE, value)
        return _over(bet.line, value, settled)

    if not market.tracked:
        return Decided(Outcome.UNTRACKED)
    if market.first_td:
        return _first_td(stat, game, settled, stale)
    if stale:
        return Decided(Outcome.UNAVAILABLE)
    value = stat.value if stat else None
    if value is None:
        if settled:
            return Decided(Outcome.NO_STAT_LINE)
        return Decided(Outcome.LIVE)
    assert bet.line is not None
    return _over(bet.line, value, settled)


def _over(line: Decimal, value: Decimal, settled: bool) -> Decided:
    if settled:
        return Decided(_result(settle_over(line, value)), value)
    if value > line:
        return Decided(Outcome.LOCKED, value)
    # The smallest whole amount that beats the line: 72.5 needs 73; a whole line 5 needs 6.
    needed = (line - value).to_integral_value(rounding="ROUND_FLOOR") + 1
    return Decided(Outcome.LIVE, value, needed)


def _first_td(stat: PlayerStat | None, game: GameState, settled: bool, stale: bool) -> Decided:
    scorer = stat.first_td_scorer if stat else None
    if scorer == UNKNOWN_SCORER:  # a touchdown we couldn't attribute: never guess who
        return Decided(Outcome.UNAVAILABLE)
    if scorer is not None:
        mine = stat is not None and scorer == stat.athlete_id
        if settled:
            return Decided(Outcome.WON if mine else Outcome.LOST)
        return Decided(Outcome.LOCKED if mine else Outcome.GONE)
    if settled:
        return Decided(Outcome.LOST)  # no touchdown in the game: nobody scored first
    return Decided(Outcome.UNAVAILABLE if stale else Outcome.LIVE)


def _result(result: LegResult) -> Outcome:
    return {LegResult.WIN: Outcome.WON, LegResult.LOSS: Outcome.LOST,
            LegResult.PUSH: Outcome.PUSH}[result]


# --- Money ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Money:
    returns: Decimal        # total back on a $10 win, stake included
    profit: Decimal
    fair_returns: Decimal | None  # the same without the book's margin, when both sides exist
    fair_probability: float | None
    implied_probability: float


def money(american: int, opposite_american: int | None) -> Money:
    decimal = odds.decimal_odds(american)
    returns = odds.payout(STAKE, decimal)
    implied = odds.implied_probability(american)
    fair_returns = fair_p = None
    if opposite_american is not None:
        fair_p = odds.no_vig(implied, odds.implied_probability(opposite_american))
        fair_returns = odds.payout(STAKE, Decimal(1) / Decimal(str(fair_p)))
    return Money(returns, returns - STAKE, fair_returns, fair_p, implied)


def settled_returns(outcome: Outcome, m: Money) -> Decimal | None:
    """What $10 actually came back as, once known."""
    match outcome:
        case Outcome.WON | Outcome.LOCKED:
            return m.returns
        case Outcome.LOST | Outcome.GONE:
            return Decimal("0.00")
        case Outcome.PUSH | Outcome.VOID:
            return STAKE
    return None
