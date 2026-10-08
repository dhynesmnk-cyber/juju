# Ported from parlaytracker@c3bd43c parlaytracker/core/live.py. Changes: imports; the freshness
# and status-text helpers come from juju.core.live, which has the same rules (ported from here);
# and its Live page is now the Watch page.
"""Live tracking read-model and rules (SPEC.md sections 8.1, 8.3 and 9.4).

Pure functions, plus loaders that read Postgres: what counts as an active event, how often each
state is polled, a leg's live value and whether it is over or under right now, how stale the
data looks, and what the Watch page (`/me`) shows. Live values are for display only: nothing
here (or in `poll_nfl_live`) ever settles a leg (constraint 2).
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from juju.core import live as core_live
from juju.core.enums import HealthState
from juju.core.live import (  # noqa: F401 (re-exported: the Watch page's rules)
    Severity,
    age_seconds,
    clock_text,
    freshness,
    no_change_minutes,
    period_label,
)
from juju.core.models import SourceHealth
from juju.tracker.models import (
    Event,
    EventStatus,
    Leg,
    LegResult,
    LegSource,
    MarketType,
    Slip,
    SlipStatus,
    Sport,
)
from juju.tracker.settlement import score_value, tuesday_deadline

# --- Which events are tracked, and how often (section 8.1) -------------------------------------

ACTIVE_BEFORE = timedelta(minutes=30)  # from 30 minutes before kickoff...
ACTIVE_AFTER = timedelta(hours=8)      # ...to 8 hours after it
_OVER = {EventStatus.FINAL, EventStatus.POSTPONED, EventStatus.CANCELLED}

SCOREBOARD_EVERY: dict[EventStatus, timedelta] = {
    EventStatus.SCHEDULED: timedelta(minutes=5),
    EventStatus.IN_PROGRESS: timedelta(seconds=30),
    EventStatus.BREAK: timedelta(minutes=2),
    EventStatus.DELAYED: timedelta(minutes=2),
}
BOX_EVERY: dict[EventStatus, timedelta] = {  # only for events with pending player legs
    EventStatus.IN_PROGRESS: timedelta(seconds=60),
    EventStatus.BREAK: timedelta(minutes=3),
    EventStatus.DELAYED: timedelta(minutes=3),
}


def is_active(event: Event, now: datetime) -> bool:
    """An NFL event that is (about to be) in play and not finished (section 8.1)."""
    return (event.sport is Sport.NFL and event.status not in _OVER
            and event.start_time - ACTIVE_BEFORE <= now <= event.start_time + ACTIVE_AFTER)


def scoreboard_interval(status: EventStatus) -> timedelta | None:
    """None: stop polling this event (`settle` takes over)."""
    return SCOREBOARD_EVERY.get(status)


def box_interval(status: EventStatus) -> timedelta | None:
    return BOX_EVERY.get(status)


# --- A leg's live value and state ----------------------------------------------------------------

TEAM_MARKETS = (MarketType.GAME_TOTAL, MarketType.TEAM_TOTAL, MarketType.ALT_SPREAD)


def score_live_value(leg: Leg, home: int | None, away: int | None) -> Decimal | None:
    """The live value of a leg that settles on the score: total, the side's score, or margin."""
    if leg.market_type not in TEAM_MARKETS or home is None or away is None:
        return None
    return Decimal(score_value(leg.market_type, leg.side, home, away))


class State(StrEnum):
    OVER = "over"      # for an alt spread: covering
    UNDER = "under"    # for an alt spread: not covering
    LEVEL = "level"    # exactly on the line: a push if it stayed
    UNKNOWN = "unknown"


def leg_state(market: MarketType, line: Decimal, value: Decimal | None) -> State:
    """Whether a leg is winning right now, in the terms of section 7.1 (an Over, or for an alt
    spread `value + line`). This is a description of the moment, not a result."""
    if value is None or market is MarketType.OTHER:
        return State.UNKNOWN
    x = value + line if market is MarketType.ALT_SPREAD else value - line
    return State.OVER if x > 0 else State.UNDER if x < 0 else State.LEVEL


# --- How fresh the data looks, and how a game's state reads (section 9.4) -----------------------
# The rules themselves are in juju.core.live (Severity, freshness, no_change_minutes,
# age_seconds, period_label, clock_text), imported above.


def game_status_text(event: Event, now: datetime) -> str:
    """'Q3 8:42', 'Halftime', 'Delayed', 'Kickoff in 12 min', 'Final'."""
    return core_live.game_status_text(event.status, event.period, event.clock_seconds,
                                      event.start_time, now)


# --- Cards ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LegView:
    leg_id: int
    leg: Leg
    tracked: bool               # an NFL leg: live values exist
    live_value: Decimal | None
    state: State
    settled: LegResult | None   # the result, once the leg has settled
    status_text: str            # the game's clock or status ("" for an untracked leg)
    provider: LegSource | None


@dataclass(frozen=True)
class Card:
    slip: Slip
    legs: list[LegView]
    progress: str | None        # "2 of 4 over, 1 lost" for a slip with several legs
    severity: Severity
    updated_seconds_ago: int | None
    provider: LegSource | None
    no_change_min: int | None


def progress_text(views: list[LegView]) -> str | None:
    """'2 of 4 over, 1 lost': legs currently winning or already won, and legs already lost."""
    if len(views) < 2:
        return None
    ok = sum(1 for v in views if v.settled is LegResult.WIN
             or (v.settled is None and v.state is State.OVER))
    lost = sum(1 for v in views if v.settled is LegResult.LOSS)
    return f"{ok} of {len(views)} over" + (f", {lost} lost" if lost else "")


_SEVERITY_ORDER = [Severity.OK, Severity.AMBER, Severity.RED]


def build_card(slip: Slip, now: datetime) -> Card | None:
    """A card for a pending slip with at least one active NFL event, else None."""
    active = [leg for leg in slip.legs if is_active(leg.event, now)]
    if not active:
        return None
    views = []
    for leg in slip.legs:
        event = leg.event
        tracked = event.sport is Sport.NFL and leg.market_type is not MarketType.OTHER
        settled = None if leg.result is LegResult.PENDING else leg.result
        value = leg.live_value
        if tracked and value is None and leg.result is LegResult.PENDING \
                and leg.market_type in TEAM_MARKETS:
            value = score_live_value(leg, event.home_score, event.away_score)
        state = leg_state(leg.market_type, leg.line, value) if tracked and leg.line is not None \
            else State.UNKNOWN
        views.append(LegView(leg.id, leg, tracked, value, state, settled,
                             game_status_text(event, now) if tracked else "", leg.live_source))
    events = {leg.event.id: leg.event for leg in active}.values()
    severity = max((freshness(e.status, e.last_polled_at, now) for e in events),
                   key=_SEVERITY_ORDER.index)
    polled = [e.last_polled_at for e in events if e.last_polled_at]
    changes = [m for e in events
               if (m := no_change_minutes(e.status, e.last_progress_at, now)) is not None]
    provider = next((leg.live_source for leg in active if leg.live_source), None)
    return Card(slip, views, progress_text(views), severity,
                age_seconds(max(polled), now) if polled else None, provider,
                max(changes) if changes else None)


def live_cards(session: Session, now: datetime) -> list[Card]:
    slips = session.scalars(
        select(Slip).where(Slip.status == SlipStatus.PENDING)
        .options(selectinload(Slip.legs).selectinload(Leg.event),
                 selectinload(Slip.sportsbook))
        .order_by(Slip.created_at, Slip.id)).all()
    cards = (build_card(s, now) for s in slips)
    return sorted((c for c in cards if c is not None),
                  key=lambda c: min(leg.event.start_time for leg in c.slip.legs))


# --- Everything ESPN is failing -------------------------------------------------------------------

ESPN_SOURCES = ("espn_web", "espn_site", "espn_cdn")


def espn_unavailable_since(rows: Iterable[SourceHealth]) -> datetime | None:
    """When every ESPN provider's breaker was open, the time the last one failed; else None.

    A half-open breaker (its open time has passed, awaiting a trial) still counts as open: the
    state clears only when a request succeeds.
    """
    espn = {r.source: r for r in rows if r.source in ESPN_SOURCES}
    if set(espn) != set(ESPN_SOURCES) or any(
            r.state is not HealthState.OPEN for r in espn.values()):
        return None
    failures = [r.last_failure_at for r in espn.values() if r.last_failure_at]
    return max(failures) if failures else None


def load_espn_unavailable_since(session: Session) -> datetime | None:
    return espn_unavailable_since(session.scalars(select(SourceHealth)))


# --- Settled in the last 7 days -------------------------------------------------------------------

SETTLED_WITHIN = timedelta(days=7)


class Verification(StrEnum):
    VERIFIED = "verified"
    AWAITING = "awaiting verification"  # nflverse may still confirm it: before the Tuesday
    UNVERIFIED = "unverified"           # past the Tuesday, or settled from one source only
    NOT_APPLICABLE = ""                 # not an NFL leg


def verification(leg: Leg, now: datetime) -> Verification:
    """The label section 9.4 gives an NFL leg (7.1): verified, awaiting verification, or
    unverified."""
    if leg.event.sport is not Sport.NFL or leg.market_type is MarketType.OTHER:
        return Verification.NOT_APPLICABLE
    if leg.verified_at is not None:
        return Verification.VERIFIED
    if leg.settlement_source is LegSource.NFLVERSE or leg.settlement_source is LegSource.MANUAL:
        return Verification.UNVERIFIED  # no second source can agree with itself
    if leg.result is LegResult.PENDING:
        return Verification.NOT_APPLICABLE
    return (Verification.AWAITING if now <= tuesday_deadline(leg.event.start_time)
            else Verification.UNVERIFIED)


@dataclass(frozen=True)
class SettledSlip:
    slip: Slip
    verifications: list[Verification]


def recent_settled(session: Session, now: datetime) -> list[SettledSlip]:
    slips = session.scalars(
        select(Slip).where(Slip.status != SlipStatus.PENDING,
                           Slip.settled_at > now - SETTLED_WITHIN)
        .options(selectinload(Slip.legs).selectinload(Leg.event),
                 selectinload(Slip.sportsbook))
        .order_by(Slip.settled_at.desc(), Slip.id.desc())).all()
    return [SettledSlip(s, [verification(leg, now) for leg in s.legs]) for s in slips]
