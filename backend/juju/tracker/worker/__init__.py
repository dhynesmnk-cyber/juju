"""The tracker's worker jobs, registered in Juju's one scheduler (`juju/worker/__main__.py`).

After parlaytracker@c3bd43c parlaytracker/worker/__main__.py, whose jobs and timings these are.
They share Juju's process, lock, router (one ESPN rate limit, one set of breakers) and Odds API
client (one key, one reserve). Their names start `tracker_`, so a failure in `source_health`
says whose it was.
"""
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine

from juju.config import Settings
from juju.ingest.odds_api import OddsApiClient
from juju.ingest.router import Breakers, EspnRouter
from juju.tracker.feed import EspnFeed, OddsFeed
from juju.tracker.nflverse import NflverseLegs
from juju.tracker.worker.closing import ClosingCapture
from juju.tracker.worker.live import PollNflLive
from juju.tracker.worker.settle import (
    Canary,
    CheckFinals,
    RecheckSettled,
    Settle,
    VerifyNfl,
    prune_samples,
)

ET = "America/New_York"
LIVE_SECONDS = 30
CAPTURE_SECONDS = 60
CHECK_FINALS_MINUTES = 15
SETTLE_MINUTES = 5
RECHECK_MINUTES = 60

Job = tuple[str, Callable[[], Any], str, dict[str, Any]]  # name, job, trigger, when


def jobs(engine: Engine, router: EspnRouter, breakers: Breakers, odds: OddsApiClient | None,
         settings: Settings) -> list[Job]:
    """Every tracker job, as (name, job, trigger, trigger arguments)."""
    feed = EspnFeed(router)

    def nflverse() -> NflverseLegs:
        return NflverseLegs(breakers)  # a fresh one per run: each dataset loads once per run

    canary = Canary(feed, nflverse)
    out: list[Job] = [
        ("tracker_poll_live", PollNflLive(engine, feed), "interval", {"seconds": LIVE_SECONDS}),
        ("tracker_check_finals", CheckFinals(engine, feed), "interval",
         {"minutes": CHECK_FINALS_MINUTES}),
        ("tracker_settle", Settle(engine, feed), "interval", {"minutes": SETTLE_MINUTES}),
        ("tracker_recheck_settled", RecheckSettled(engine, feed), "interval",
         {"minutes": RECHECK_MINUTES}),
        ("tracker_verify_nfl", VerifyNfl(engine, nflverse), "cron",
         {"hour": 10, "minute": 0, "timezone": ET}),
        ("tracker_canary", canary, "cron", {"hour": 9, "minute": 0, "timezone": ET}),
        ("tracker_canary_at_startup", canary, "date", {}),  # as soon as the scheduler starts
        ("tracker_prune_samples", lambda: prune_samples(engine), "cron",
         {"hour": 4, "minute": 0, "timezone": "UTC"}),
    ]
    if odds is not None:
        out.append(("tracker_capture_closing",
                    ClosingCapture(engine, OddsFeed(odds, settings.books), breakers,
                                   settings.odds_api_reserve),
                    "interval", {"seconds": CAPTURE_SECONDS}))
    return out
