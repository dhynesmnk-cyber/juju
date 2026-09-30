# Ported from parlaytracker@c3bd43c parlaytracker/ingest/guards.py. Plausibility bounds are keyed
# by Juju's Stat instead of MarketType; everything else is unchanged.
"""Integrity guards (SPEC.md section 8.3): never show or store data older than what is held.

Pure functions; the router and the jobs call them. Nothing here touches the database.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from juju.core.enums import EventStatus, Sport, Stat

# --- Progress key -----------------------------------------------------------------------------

_STATUS_RANK = {
    EventStatus.SCHEDULED: 0,
    EventStatus.IN_PROGRESS: 1, EventStatus.BREAK: 1, EventStatus.DELAYED: 1,
    EventStatus.FINAL: 2,
}
# Regulation period length in seconds. Only the ordering inside one period matters, so the
# exact length never decides anything; MLB has no clock, so its key is (rank, inning, 0).
PERIOD_SECONDS = {Sport.NFL: 900, Sport.NBA: 720, Sport.NHL: 1200, Sport.MLB: 0}
FROZEN_AFTER = timedelta(minutes=5)
PROBE_EVERY = timedelta(minutes=5)

ProgressKey = tuple[int, int, int]


def progress_key(sport: Sport, status: EventStatus, period: int | None,
                 clock_seconds: int | None) -> ProgressKey | None:
    """(status rank, period, seconds elapsed in the period), or None for a status with no
    rank (postponed, cancelled): those aren't progress."""
    rank = _STATUS_RANK.get(status)
    if rank is None:
        return None
    period = period or 0
    if clock_seconds is None or sport is Sport.MLB:
        elapsed = 0
    else:
        elapsed = PERIOD_SECONDS[sport] - clock_seconds
    return rank, period, elapsed


class Verdict(StrEnum):
    STALE = "stale"          # lower than what is held: a cached response, discard it
    CORRECTION = "correction"  # equal key; accept, and log it if the values differ
    ADVANCE = "advance"      # higher: accept it and set last_progress_at


def compare(new: ProgressKey, stored: ProgressKey | None) -> Verdict:
    if stored is None or new > stored:
        return Verdict.ADVANCE
    return Verdict.STALE if new < stored else Verdict.CORRECTION


def accept_status_change(stored: EventStatus, new: EventStatus) -> bool:
    """`final` never goes back to in play (section 8.3). Postponed and cancelled games may
    be rescheduled, so they may move to any status."""
    return not (stored is EventStatus.FINAL and new in (
        EventStatus.SCHEDULED, EventStatus.IN_PROGRESS, EventStatus.BREAK, EventStatus.DELAYED))


# --- Frozen feed ------------------------------------------------------------------------------


def is_frozen(status: EventStatus, last_progress_at: datetime | None, now: datetime) -> bool:
    """`in_progress` (not a break or a delay) with no progress for more than 5 minutes.

    A stopped clock during a review or an injury is still "frozen" by this test; what
    distinguishes it is the probe: if the next provider shows no higher key either, the game
    itself is stopped and nothing changes (the caller decides, from the probe).
    """
    return (status is EventStatus.IN_PROGRESS and last_progress_at is not None
            and now - last_progress_at > FROZEN_AFTER)


def may_probe(last_probe_at: datetime | None, now: datetime) -> bool:
    """At most one probe per event every 5 minutes."""
    return last_probe_at is None or now - last_probe_at >= PROBE_EVERY


# --- Plausibility -----------------------------------------------------------------------------


class ImplausibleError(ValueError):
    """A value outside the bounds of section 6.1: reject the whole response."""


NFL_TEAM_SCORE = (0, 99)
_BOUNDS: dict[Stat, tuple[int, int]] = {
    Stat.RECEIVING_YARDS: (-30, 400),
    Stat.RUSHING_YARDS: (-30, 400),
    Stat.PASSING_YARDS: (-30, 700),
    Stat.LONGEST_RECEPTION: (-30, 110),
    Stat.LONGEST_RUSH: (-30, 110),
    Stat.RECEPTIONS: (0, 25),
    Stat.RUSH_ATTEMPTS: (0, 60),
    Stat.PASS_COMPLETIONS: (0, 70),
    Stat.PASS_ATTEMPTS: (0, 90),
    Stat.PASS_TDS: (0, 10),
    Stat.TOUCHDOWNS: (0, 8),
    Stat.RUSH_TDS: (0, 8),
    Stat.RECEIVING_TDS: (0, 8),
    Stat.INTERCEPTIONS_THROWN: (0, 10),
    Stat.FIELD_GOALS: (0, 10),
    Stat.KICKING_POINTS: (0, 40),
    Stat.SACKS: (0, 10),
    Stat.TACKLES_ASSISTS: (0, 40),
    Stat.SOLO_TACKLES: (0, 30),
    Stat.DEF_INTERCEPTIONS: (0, 5),
}


def check_score(sport: Sport, score: int | None, what: str = "team score") -> None:
    if sport is Sport.NFL and score is not None and not (
            NFL_TEAM_SCORE[0] <= score <= NFL_TEAM_SCORE[1]):
        raise ImplausibleError(f"NFL {what} {score} is outside {NFL_TEAM_SCORE}")


def check_stats(stats: dict[Stat, dict[str, Decimal]]) -> None:
    for stat, by_athlete in stats.items():
        bounds = _BOUNDS.get(stat)
        if bounds is None:
            continue
        for athlete, value in by_athlete.items():
            if not bounds[0] <= value <= bounds[1]:
                raise ImplausibleError(
                    f"{stat} {value} for athlete {athlete} is outside {bounds}")

