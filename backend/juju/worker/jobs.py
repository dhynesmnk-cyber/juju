# `heartbeat` and `guarded` are ported from parlaytracker@c3bd43c parlaytracker/worker/jobs.py.
"""Worker plumbing: the heartbeat, the wrapper that keeps a failing job from reaching the
scheduler, and the T-44 check that makes a missed capture visible."""
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from juju.core.enums import HealthState
from juju.core.models import Game, SourceHealth
from juju.ingest.router import Breakers
from juju.worker.capture import has_on_time_snapshot

log = logging.getLogger("juju.worker")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def heartbeat(engine: Engine, now: datetime | None = None,
              breakers: Breakers | None = None) -> None:
    """Show the worker is alive, and write every provider's breaker state and counters."""
    now = now or _utcnow()
    stmt = insert(SourceHealth).values(source="worker", state=HealthState.OK, last_success_at=now,
                                       consecutive_failures=0, requests_last_hour=0,
                                       errors_last_hour=0)
    stmt = stmt.on_conflict_do_update(index_elements=[SourceHealth.source],
                                      set_={"last_success_at": now, "state": HealthState.OK})
    with engine.begin() as conn:
        conn.execute(stmt)
    if breakers is not None:
        breakers.persist()


def guarded(engine: Engine, name: str, job: Callable[[], object]) -> Callable[[], None]:
    """Wrap a job: log and record any error, never raise."""
    def run() -> None:
        try:
            job()
        except Exception as e:
            log.exception("job %s failed", name)
            try:
                with engine.begin() as conn:
                    conn.execute(insert(SourceHealth).values(
                        source="worker", state=HealthState.OK, last_error=f"{name}: {e}"[:500],
                        consecutive_failures=0, requests_last_hour=0, errors_last_hour=0,
                    ).on_conflict_do_update(index_elements=[SourceHealth.source],
                                            set_={"last_error": f"{name}: {e}"[:500]}))
            except Exception:
                log.exception("could not record the error from job %s", name)
    return run


def check_captures(engine: Engine, now: datetime | None = None) -> list[str]:
    """Games past T-44 in the last 6 hours with no on-time snapshot. They are written to the
    `archive` health row, so the health report and the alerts see them; `repair_gaps` fills
    them from the vendor's history."""
    now = now or _utcnow()
    with Session(engine) as session:
        games = session.scalars(select(Game).where(
            Game.odds_event_id.is_not(None),
            Game.commence_time.between(now - timedelta(hours=6), now + timedelta(minutes=44))))
        missing = [g.label for g in games if not has_on_time_snapshot(session, g)]
    state = HealthState.DEGRADED if missing else HealthState.OK
    error = ("No on-time T-45 price for: " + ", ".join(missing))[:500] if missing else None
    if missing:
        log.error("%s", error)
    values = dict(source="archive", state=state, last_error=error, consecutive_failures=0,
                  requests_last_hour=0, errors_last_hour=0,
                  **({"last_success_at": now} if not missing else {"last_failure_at": now}))
    with engine.begin() as conn:
        conn.execute(insert(SourceHealth).values(**values).on_conflict_do_update(
            index_elements=[SourceHealth.source],
            set_={k: v for k, v in values.items() if k != "source"}))
    return missing
