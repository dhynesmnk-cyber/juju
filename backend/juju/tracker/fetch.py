# Ported from parlaytracker@c3bd43c parlaytracker/ingest/espn.py: its fetch helpers. Changes: the
# caller passes the router (Juju's API never calls ESPN, so there is no module-level one).
"""ESPN scoreboards and rosters for the tracker: the importer now, the Log form's pickers later.

Through Juju's router, so the same failover and failure rules apply. Every failure comes back
as a FetchError (or RateLimited), which is what the tracker's callers handle.
"""
from datetime import date

from juju.core.enums import FailureKind, Sport
from juju.ingest import espn
from juju.ingest.http import FetchError
from juju.ingest.router import AllProvidersFailed, EspnRouter


def _as_fetch_error(path: str, e: AllProvidersFailed) -> FetchError:
    return FetchError(path, e.kind or FailureKind.TRANSIENT, str(e))


def scoreboard(router: EspnRouter, sport: Sport, day: date,
               max_wait: float = 0.0) -> espn.ScoreboardResult:
    try:
        return router.scoreboard(day, max_wait, sport=sport).value
    except AllProvidersFailed as e:
        raise _as_fetch_error(f"{espn.SPORT_PATHS[sport]}/scoreboard", e) from e


def roster(router: EspnRouter, sport: Sport, team_id: str,
           max_wait: float = 0.0) -> list[espn.RosterPlayer]:
    try:
        return router.roster(team_id, max_wait, sport=sport).value
    except AllProvidersFailed as e:
        raise _as_fetch_error(f"{espn.SPORT_PATHS[sport]}/teams/{team_id}/roster", e) from e
