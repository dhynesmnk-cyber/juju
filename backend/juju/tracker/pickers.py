# Ported from parlaytracker@c3bd43c parlaytracker/app/common.py (the cached `scoreboard` and
# `roster`) and app/components/slip_form.py (`_games`, `_players`, `_game_label`). Changes: an
# in-process cache with the same lifetimes stands in for st.cache_data; the lookups are passed in
# (the API gives its own router); and the time zone is an argument.
"""The Log form's pickers: a day's games and a game's players, from ESPN (SPEC.md section 9.3).

These are the tracker's only reads of a data provider made for a user's request. They go
through Juju's router, so its rate limits and failover apply; they wait at most `WEB_MAX_WAIT`
for a rate-limit slot; and an answer is kept for 10 minutes (a scoreboard) or 12 hours (a
roster), so two people filling in slips cost ESPN a few requests an hour. A failure is never
kept: it comes back as a message the form shows, and the next request tries again.
"""
import threading
import time
from collections.abc import Callable, Hashable
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from juju.core.enums import EventStatus, Sport
from juju.ingest import espn
from juju.ingest.http import FetchError, RateLimited
from juju.tracker.format import fmt_time

# A web request may wait briefly for a rate-limit slot; the worker never waits (section 6.1).
WEB_MAX_WAIT = 5.0
SCOREBOARD_SECONDS = 600
ROSTER_SECONDS = 12 * 3600

Scoreboard = Callable[[Sport, date], list[espn.Game]]
Roster = Callable[[Sport, str], list[espn.RosterPlayer]]
# {athlete id: (name, label)}, away team first: what the player picker lists.
Players = dict[str, tuple[str, str]]


class TtlCache:
    """Answers kept for `seconds`; errors are raised to the caller and never kept."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self._clock = clock
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, load: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and now - hit[0] < self.seconds:
                return hit[1]
        value = load()  # outside the lock: a slow ESPN call holds up no one else
        with self._lock:
            self._items[key] = (now, value)
        return value


def game_label(game: espn.Game, tz: ZoneInfo) -> str:
    status = "" if game.status is EventStatus.SCHEDULED else f" · {game.status_detail}"
    return f"{game.label} · {fmt_time(game.start_time, tz)}{status}"


class Pickers:
    def __init__(self, scoreboard: Scoreboard, roster: Roster,
                 clock: Callable[[], float] = time.monotonic):
        self._scoreboard = scoreboard
        self._roster = roster
        self._boards = TtlCache(SCOREBOARD_SECONDS, clock)
        self._rosters = TtlCache(ROSTER_SECONDS, clock)

    def games(self, sport: Sport, day: date) -> tuple[list[espn.Game], str | None]:
        """A game day's games, or none and why not."""
        try:
            return self._boards.get((sport, day), lambda: self._scoreboard(sport, day)), None
        except (FetchError, RateLimited) as e:
            return [], (f"Couldn't load {sport.value.upper()} games from ESPN ({e}). "
                        "Try again shortly.")

    def game(self, sport: Sport, day: date, espn_event_id: str
             ) -> tuple[espn.Game | None, str | None]:
        games, problem = self.games(sport, day)
        return next((g for g in games if g.espn_event_id == espn_event_id), None), problem

    def players(self, sport: Sport, game: espn.Game) -> tuple[Players, str | None]:
        """Both teams' players, away team first, or as many as loaded and why the rest didn't."""
        players: Players = {}
        for team in (game.away, game.home):
            try:
                roster = self._rosters.get((sport, team.espn_id),
                                           lambda t=team: self._roster(sport, t.espn_id))
            except (FetchError, RateLimited) as e:
                return players, f"Couldn't load the {team.abbreviation} roster from ESPN ({e})."
            for p in roster:
                note = " · out" if p.unavailable else ""
                players.setdefault(p.espn_athlete_id,
                                   (p.name, f"{p.label} · {team.abbreviation}{note}"))
        return players, None
