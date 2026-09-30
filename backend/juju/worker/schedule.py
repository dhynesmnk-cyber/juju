"""Keeping the game list and rosters current: `sync_schedule` and `sync_rosters`.

The Odds API's events list is free and names the games we can price. ESPN's scoreboard gives
each game's ESPN id and team ids (for box scores and rosters). Games are matched by both team
names and a start time within 3 hours (parlaytracker's `same_game`).
"""
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from juju.core.enums import EventStatus
from juju.core.models import Game, Player
from juju.ingest import espn
from juju.ingest.http import FetchError, RateLimited
from juju.ingest.odds_api import OddsSource
from juju.ingest.resolve import same_game
from juju.ingest.router import AllProvidersFailed, EspnRouter

log = logging.getLogger("juju.schedule")

LOOK_AHEAD = timedelta(days=8)
ROSTER_WINDOW = timedelta(hours=36)
ROSTER_MAX_AGE = timedelta(hours=20)
# A scheduled-looking game this long after its start is left to the live loop.
_ESPN_ERRORS = (AllProvidersFailed, RateLimited, FetchError)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def upsert_odds_events(session: Session, events, now: datetime) -> int:
    """Create or update games from The Odds API's list. Kickoff times follow the vendor."""
    changed = 0
    for e in events:
        game = session.scalar(select(Game).where(Game.odds_event_id == e.id))
        if game is None:
            game = next((g for g in session.scalars(select(Game).where(
                Game.odds_event_id.is_(None),
                Game.commence_time.between(e.commence_time - timedelta(hours=3),
                                           e.commence_time + timedelta(hours=3))))
                if same_game(g.home_team, g.away_team, g.commence_time, e.home_team,
                             e.away_team, e.commence_time)), None)
        if game is None:
            game = Game(home_team=e.home_team, away_team=e.away_team,
                        commence_time=e.commence_time)
            session.add(game)
        if game.odds_event_id != e.id or game.commence_time != e.commence_time:
            changed += 1
        game.odds_event_id = e.id
        if game.status is EventStatus.SCHEDULED:
            game.commence_time = e.commence_time
    session.flush()
    return changed


def apply_scoreboard(session: Session, games: list[espn.Game]) -> None:
    """Attach ESPN ids and team ids; a game ESPN lists that we don't know yet is added."""
    for g in games:
        game = session.scalar(select(Game).where(Game.espn_event_id == g.espn_event_id))
        if game is None:
            game = next((x for x in session.scalars(select(Game).where(
                Game.espn_event_id.is_(None),
                Game.commence_time.between(g.start_time - timedelta(hours=3),
                                           g.start_time + timedelta(hours=3))))
                if same_game(x.home_team, x.away_team, x.commence_time, g.home.name,
                             g.away.name, g.start_time)), None)
        if game is None:
            game = Game(home_team=g.home.name, away_team=g.away.name,
                        commence_time=g.start_time)
            session.add(game)
        game.espn_event_id = g.espn_event_id
        game.home_abbr, game.away_abbr = g.home.abbreviation, g.away.abbreviation
        game.home_espn_id, game.away_espn_id = g.home.espn_id, g.away.espn_id
    session.flush()


class SyncSchedule:
    def __init__(self, engine: Engine, odds: OddsSource | None, router: EspnRouter,
                 clock: Callable[[], datetime] = _utcnow):
        self._engine = engine
        self._odds = odds
        self._router = router
        self._clock = clock

    def __call__(self) -> None:
        now = self._clock()
        with Session(self._engine, expire_on_commit=False) as session:
            if self._odds is not None:
                try:
                    upsert_odds_events(session, self._odds.events(), now)
                except Exception as e:  # noqa: BLE001 - ESPN still runs; logged, retried
                    log.warning("Odds API events unavailable: %s", e)
            session.commit()
            days = self._days(session, now)
            for day in days:
                try:
                    board = self._router.scoreboard(day, max_wait=5).value
                except _ESPN_ERRORS as e:
                    log.warning("scoreboard %s unavailable: %s", day, e)
                    continue
                apply_scoreboard(session, board.games)
                session.commit()

    @staticmethod
    def _days(session: Session, now: datetime) -> list[date]:
        starts = session.scalars(select(Game.commence_time).where(
            Game.commence_time.between(now - timedelta(days=1), now + LOOK_AHEAD))).all()
        days = {espn.game_day(s) for s in starts} | {espn.today_game_day(now)}
        return sorted(days)


class SyncRosters:
    """Rosters for teams playing within 36 hours, refreshed at most every 20 hours."""

    def __init__(self, engine: Engine, router: EspnRouter,
                 clock: Callable[[], datetime] = _utcnow):
        self._engine = engine
        self._router = router
        self._clock = clock

    def __call__(self) -> int:
        now = self._clock()
        refreshed = 0
        with Session(self._engine, expire_on_commit=False) as session:
            teams: set[str] = set()
            for g in session.scalars(select(Game).where(
                    Game.commence_time.between(now - timedelta(hours=6), now + ROSTER_WINDOW),
                    or_(Game.home_espn_id.is_not(None), Game.away_espn_id.is_not(None)))):
                teams |= {t for t in (g.home_espn_id, g.away_espn_id) if t}
            for team in sorted(teams):
                newest = session.scalar(select(Player.updated_at).where(
                    Player.team_espn_id == team).order_by(Player.updated_at.desc()).limit(1))
                if newest is not None and now - newest < ROSTER_MAX_AGE:
                    continue
                try:
                    roster = self._router.roster(team, max_wait=5).value
                except _ESPN_ERRORS as e:
                    log.warning("roster %s unavailable: %s", team, e)
                    continue
                upsert_roster(session, team, roster, now)
                session.commit()
                refreshed += 1
        return refreshed


def upsert_roster(session: Session, team: str, roster: list[espn.RosterPlayer],
                  now: datetime) -> None:
    for p in roster:
        row = session.get(Player, p.espn_athlete_id)
        if row is None:
            row = Player(espn_athlete_id=p.espn_athlete_id)
            session.add(row)
        row.name, row.team_espn_id, row.position = p.name, team, p.position
        row.unavailable, row.updated_at = p.unavailable, now
    session.flush()
