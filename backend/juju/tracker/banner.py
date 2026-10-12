# Ported from parlaytracker@c3bd43c parlaytracker/app/common.py: `health_banner`. Changes: a pure
# function over the `source_health` rows that returns its messages instead of drawing them, with
# the time zone and the reserve as arguments.
"""The tracker's health banner (SPEC.md section 9.2): has the worker stopped, are credits low,
is a data source failing? Shown on every tracker page."""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from juju.core.enums import FailureKind, HealthState
from juju.core.models import SourceHealth
from juju.tracker.format import fmt_time

WORKER_STALE_AFTER = timedelta(minutes=3)
SOURCE_OPEN_WARN_AFTER = timedelta(minutes=5)
# One capture costs a credit per market, so warn a few calls before the reserve is reached.
MARKETS_PER_CAPTURE = 10


@dataclass(frozen=True)
class Banner:
    level: str  # "warning" or "error"
    text: str


def health_banner(rows: Iterable[SourceHealth], now: datetime, reserve: int,
                  tz: ZoneInfo) -> list[Banner]:
    """Warn when the worker has stopped or a data source is failing."""
    out: list[Banner] = []
    by_source = {r.source: r for r in rows}
    worker = by_source.pop("worker", None)
    if worker is None or worker.last_success_at is None:
        out.append(Banner("warning", "The background worker has never run on this database."))
    elif now - worker.last_success_at > WORKER_STALE_AFTER:
        out.append(Banner("warning", f"The background worker was last seen "
                                     f"{fmt_time(worker.last_success_at, tz)}. Live data and "
                                     "closing lines are paused until it's back."))
    odds = by_source.get("odds_api")
    if odds is not None and odds.quota_remaining is not None and (
            odds.quota_remaining < reserve + MARKETS_PER_CAPTURE):
        out.append(Banner("warning", f"The Odds API has {odds.quota_remaining} credits left this "
                                     f"month. Closing lines stop being captured at {reserve}; "
                                     "enter them by hand on the Review page after that."))
    for row in by_source.values():
        if row.state is not HealthState.OPEN:
            continue
        if row.failure_kind is FailureKind.SCHEMA:
            out.append(Banner("error", f"{row.source}: the data format changed; the parser "
                                       "needs updating."))
            continue
        if row.open_until is not None and row.open_until <= now:
            continue  # half-open: waiting for a trial, not failing now. A fallback provider
            # that was blocked earlier isn't retried while the primary works, so its row
            # would otherwise read "failing" until the next daily canary.
        failing_since = row.last_success_at or row.last_failure_at
        if failing_since and now - failing_since > SOURCE_OPEN_WARN_AFTER:
            out.append(Banner("warning", f"{row.source} is failing ({row.failure_kind}); last "
                                         f"worked {fmt_time(row.last_success_at, tz)}."))
    return out
