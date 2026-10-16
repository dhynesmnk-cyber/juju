"""The worker: `python -m juju.worker`. The only process that calls data providers.

After parlaytracker@c3bd43c parlaytracker/worker/__main__.py: a Postgres advisory lock (exactly
one worker: two would spend Odds API credits twice), a BlockingScheduler with
coalesce=True, max_instances=1, and every job wrapped by `guarded`. On Fly.io this process
never scales to zero (docs/deploy.md).

The tracker's jobs (`juju/tracker/worker/`) run here too, on the same router and Odds API client.
The router keeps raw samples (`raw_samples`): the last few failures per provider, and every
response for the games in RECORD_EVENT_IDS.
"""
import logging
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import Connection, Engine, text

from juju.config import Settings, get_settings
from juju.db import make_engine
from juju.ingest.nflverse import NflverseData
from juju.ingest.odds_api import OddsApiClient
from juju.ingest.router import Breakers, EspnRouter
from juju.tracker import worker as tracker
from juju.tracker.worker.settle import sample_sink
from juju.worker.archive import remap_prices, remap_recent
from juju.worker.capture import CaptureT45, RepairGaps
from juju.worker.jobs import check_captures, guarded, heartbeat
from juju.worker.live import PollLive
from juju.worker.schedule import SyncRosters, SyncSchedule
from juju.worker.verify import VerifyGames

log = logging.getLogger("juju.worker")

LOCK_KEY = 5_858_104_517  # any fixed number unique to Juju
HEARTBEAT_SECONDS = 30
CAPTURE_SECONDS = 10
LIVE_SECONDS = 5
SCHEDULE_MINUTES = 30
ROSTER_MINUTES = 60
REPAIR_MINUTES = 5
CHECK_MINUTES = 1
# The next-day check, after nflverse's overnight publish; the afternoon run catches a late one.
VERIFY_HOURS_ET = "10,16"


def try_lock(conn: Connection) -> bool:
    locked = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).scalar()
    conn.commit()
    return bool(locked)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def at_startup(engine: Engine, schedule: Callable[[], object], rosters: Callable[[], object],
               clock: Callable[[], datetime] = _utcnow) -> Callable[[], None]:
    """Games, then rosters, then the stored prices whose player name didn't match before: in
    that order, so a capture soon after a restart finds the rosters it matches names against."""
    def run() -> None:
        schedule()
        rosters()
        linked = remap_recent(engine, clock())
        if linked:
            log.info("linked %d stored prices to their players", linked)
    return run


def build_scheduler(engine: Engine, breakers: Breakers, settings: Settings,
                    odds: OddsApiClient | None) -> BlockingScheduler:
    scheduler = BlockingScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30})
    router = EspnRouter(breakers, sample_sink=sample_sink(engine),
                        record_event_ids=frozenset(settings.record_event_ids))

    def add(name: str, job, trigger: str, **when) -> None:
        scheduler.add_job(guarded(engine, name, job), trigger, id=name, name=name, **when)

    add("heartbeat", lambda: heartbeat(engine, breakers=breakers), "interval",
        seconds=HEARTBEAT_SECONDS)
    schedule = SyncSchedule(engine, odds, router)
    rosters = SyncRosters(engine, router)
    add("sync_schedule", schedule, "interval", minutes=SCHEDULE_MINUTES)
    add("sync_rosters", rosters, "interval", minutes=ROSTER_MINUTES)
    add("sync_at_startup", at_startup(engine, schedule, rosters), "date")
    add("poll_live", PollLive(engine, router, odds, reserve=settings.odds_api_reserve),
        "interval", seconds=LIVE_SECONDS)
    add("check_captures", lambda: check_captures(engine), "interval", minutes=CHECK_MINUTES)
    verify = VerifyGames(engine, lambda: NflverseData(breakers))
    add("verify_games", verify, "cron", hour=VERIFY_HOURS_ET, minute=7,
        timezone="America/New_York")
    add("verify_games_at_startup", verify, "date")
    if odds is not None:
        add("capture_t45", CaptureT45(engine, odds, settings.books, settings.odds_api_reserve,
                                      reset_day=settings.odds_api_reset_day,
                                      games_per_week=settings.games_per_week,
                                      before=remap_prices),
            "interval", seconds=CAPTURE_SECONDS)
        add("repair_gaps", RepairGaps(engine, odds, settings.books, settings.odds_api_reserve,
                                      settings.repair_days), "interval", minutes=REPAIR_MINUTES)
    for name, job, trigger, when in tracker.jobs(engine, router, breakers, odds, settings):
        add(name, job, trigger, **when)
    return scheduler


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    engine = make_engine()
    lock_conn = engine.connect()  # held open for the life of the process
    if not try_lock(lock_conn):
        log.error("another worker already holds the lock; exiting")
        return 1
    settings = get_settings()
    breakers = Breakers(engine)
    breakers.load()
    odds = None
    if settings.odds_api_key is not None:
        odds = OddsApiClient(settings.odds_api_key.get_secret_value(), breakers,
                             reset_day=settings.odds_api_reset_day)
    else:
        log.warning("ODDS_API_KEY is not set: nothing will be archived")
    scheduler = build_scheduler(engine, breakers, settings, odds)
    signal.signal(signal.SIGTERM, lambda *_: scheduler.shutdown(wait=False))
    signal.signal(signal.SIGINT, lambda *_: scheduler.shutdown(wait=False))
    guarded(engine, "heartbeat", lambda: heartbeat(engine, breakers=breakers))()
    log.info("worker started (archive %s)", "on" if odds else "off: no ODDS_API_KEY")
    scheduler.start()
    log.info("worker stopped")
    lock_conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
