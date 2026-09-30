"""Enums shared by the models, the ingest layer and the API.

The first group keeps parlaytracker's names and values, so the ported code reads the same.
"""
import enum


class Sport(enum.StrEnum):
    # Juju is NFL-only; the other values stay so ported code (guards, espn) keeps its shape.
    NFL = "nfl"
    NBA = "nba"
    MLB = "mlb"
    NHL = "nhl"


class EventStatus(enum.StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    BREAK = "break"      # halftime, end of period
    DELAYED = "delayed"  # weather or other stoppage
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class DataSource(enum.StrEnum):
    ESPN_WEB = "espn_web"    # site.web.api.espn.com
    ESPN_SITE = "espn_site"  # site.api.espn.com
    ESPN_CDN = "espn_cdn"    # cdn.espn.com
    ODDS_API = "odds_api"    # The Odds API /scores: licensed, game state and scores only
    NFLVERSE = "nflverse"


class HealthState(enum.StrEnum):
    OK = "ok"
    DEGRADED = "degraded"  # recent failures, breaker still closed
    OPEN = "open"          # breaker open until open_until


class FailureKind(enum.StrEnum):
    TRANSIENT = "transient"
    BLOCKED = "blocked"
    THROTTLED = "throttled"
    SCHEMA = "schema"
    IMPLAUSIBLE = "implausible"
    FROZEN = "frozen"


class LegResult(enum.StrEnum):
    PENDING = "pending"
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


# --- Juju's own ---------------------------------------------------------------------------


class SnapshotSource(enum.StrEnum):
    LIVE = "live"              # captured by our worker before kickoff
    HISTORICAL = "historical"  # The Odds API's own archive (gap repair, backfill)


class Stat(enum.StrEnum):
    """A player statistic Juju can follow live, from ESPN's box score."""
    RECEPTIONS = "receptions"
    RECEIVING_YARDS = "receiving_yards"
    RECEIVING_TDS = "receiving_tds"
    LONGEST_RECEPTION = "longest_reception"
    RUSHING_YARDS = "rushing_yards"
    RUSH_ATTEMPTS = "rush_attempts"
    RUSH_TDS = "rush_tds"
    LONGEST_RUSH = "longest_rush"
    PASSING_YARDS = "passing_yards"
    PASS_COMPLETIONS = "pass_completions"
    PASS_ATTEMPTS = "pass_attempts"
    PASS_TDS = "pass_tds"
    INTERCEPTIONS_THROWN = "interceptions_thrown"
    TOUCHDOWNS = "touchdowns"  # rushing + receiving + returns + defensive, never passing
    FIELD_GOALS = "field_goals"
    KICKING_POINTS = "kicking_points"
    SACKS = "sacks"
    TACKLES_ASSISTS = "tackles_assists"
    SOLO_TACKLES = "solo_tackles"
    DEF_INTERCEPTIONS = "def_interceptions"


class PlayKind(enum.StrEnum):
    """What a play was, as far as which bets it touches."""
    TOUCHDOWN = "touchdown"  # the player scored (rushing, receiving or return)
    PASS_TD = "pass_td"      # the player threw a touchdown
    RUN = "run"
    CATCH = "catch"
    PASS = "pass"
    FIELD_GOAL = "field_goal"
    INTERCEPTION = "interception"  # thrown by the player
    DEF_INTERCEPTION = "def_interception"
    SACK = "sack"
    UNKNOWN = "unknown"
