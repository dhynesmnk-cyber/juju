# Ported from parlaytracker@c3bd43c parlaytracker/core/live.py: the freshness and status-text
# rules only (pure). `game_status_text` takes the fields it needs instead of an Event.
"""How fresh live data looks, and how a game's state reads."""
from datetime import datetime, timedelta
from enum import StrEnum

from juju.core.enums import EventStatus


class Severity(StrEnum):
    OK = "ok"
    AMBER = "amber"
    RED = "red"


# (amber after, red after). In play the spec says 2 and 5 minutes. A break or a delay is polled
# every 2 minutes, so the same limits would always be amber: they are stretched to match.
_FRESHNESS = {
    EventStatus.IN_PROGRESS: (timedelta(minutes=2), timedelta(minutes=5)),
    EventStatus.BREAK: (timedelta(minutes=4), timedelta(minutes=7)),
    EventStatus.DELAYED: (timedelta(minutes=4), timedelta(minutes=7)),
}
NO_CHANGE_AFTER = timedelta(minutes=5)  # section 8.3: the same as the frozen-feed rule


def freshness(status: EventStatus, last_polled_at: datetime | None, now: datetime) -> Severity:
    """Green, amber after 2 minutes and red after 5 while a game is in progress. A game that
    hasn't started isn't polled fast, so it is never coloured."""
    limits = _FRESHNESS.get(status)
    if limits is None:
        return Severity.OK
    if last_polled_at is None:
        return Severity.RED
    age = now - last_polled_at
    return Severity.RED if age > limits[1] else Severity.AMBER if age > limits[0] else Severity.OK


def no_change_minutes(status: EventStatus, last_progress_at: datetime | None,
                      now: datetime) -> int | None:
    """Minutes since the game clock last moved, once it is more than 5 (section 8.3). Only for
    a game in progress: a break or a delay is expected to stand still."""
    if status is not EventStatus.IN_PROGRESS or last_progress_at is None:
        return None
    age = now - last_progress_at
    return int(age.total_seconds() // 60) if age > NO_CHANGE_AFTER else None


def age_seconds(moment: datetime | None, now: datetime) -> int | None:
    return None if moment is None else max(0, int((now - moment).total_seconds()))


def period_label(period: int | None) -> str:
    if not period:
        return ""
    return f"Q{period}" if period <= 4 else "OT" if period == 5 else f"{period - 4}OT"


def clock_text(clock_seconds: int | None) -> str:
    return "" if clock_seconds is None else f"{clock_seconds // 60}:{clock_seconds % 60:02d}"


def game_status_text(status: EventStatus, period: int | None, clock_seconds: int | None,
                     start_time: datetime, now: datetime) -> str:
    """'Q3 8:42', 'Halftime', 'Delayed', 'Kickoff in 12 min', 'Final'."""
    match status:
        case EventStatus.IN_PROGRESS:
            return f"{period_label(period)} {clock_text(clock_seconds)}".strip() \
                or "In progress"
        case EventStatus.BREAK:
            return "Halftime" if period == 2 else \
                f"End of {period_label(period)}".strip()
        case EventStatus.DELAYED:
            return "Delayed"
        case EventStatus.SCHEDULED:
            minutes = int((start_time - now).total_seconds() // 60)
            return f"Kickoff in {minutes} min" if minutes > 0 else "About to start"
        case _:
            return status.value.capitalize()
