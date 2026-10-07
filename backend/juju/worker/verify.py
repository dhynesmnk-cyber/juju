"""The next-day check against nflverse (docs/GOALS.md section 5: "awaiting verification" and
"corrected").

After parlaytracker@c3bd43c parlaytracker/worker/settle.py `VerifyNfl`, which it follows on the
rules: nflverse is read at most once per run, a game nflverse hasn't published is left for the
next run, and players map by ID only.

Juju differs in what a disagreement does. parlaytracker sends it to a person (Review). Juju has
no one to review it, and books settle on the official stats, so nflverse's value replaces the
live feed's, the change is kept in `stat_corrections`, and the card says what changed ("a settled
payout can be re-opened and re-labelled if the official stat changes", GOALS v1 section 9).
The one exception is the final score: if nflverse's differs, nflverse or the match is wrong, so
nothing is applied and the reason goes to `games.last_error`.

Games verified in a run then get their exact deciding plays from the play-by-play, which also
confirms the longest rush and catch and the first touchdown's scorer (`worker/deciding.py`).
nflverse sometimes publishes a game's play-by-play after its stats: a game verified without it
is read on a later run (`games.plays_checked_at` still unset), until it is a week old.
"""
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from juju.core.enums import DataSource, EventStatus, Stat
from juju.core.models import Game, LiveStat, StatCorrection
from juju.ingest.nflverse import NflverseData, NflverseError, nfl_season, stat_values
from juju.ingest.router import ProviderOpen
from juju.worker import deciding

log = logging.getLogger("juju.verify")

VERIFY_AFTER = timedelta(hours=2)   # after the final whistle; nflverse publishes overnight
VERIFY_WITHIN = timedelta(days=7)   # after kickoff; older games stay as they are


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class VerifySummary:
    verified: int = 0   # games checked
    corrected: int = 0  # stat values replaced by nflverse's
    waiting: int = 0    # games nflverse hasn't published yet
    disputed: int = 0   # games whose final score disagrees: nothing applied
    plays: int = 0      # exact deciding plays found in the play-by-play
    late: int = 0       # games verified on an earlier run whose play-by-play was read now


class VerifyGames:
    """Twice a day (worker/__main__.py), for final games from the last week not yet checked,
    and for that week's verified games whose play-by-play hasn't been read yet."""

    def __init__(self, engine: Engine, nflverse: Callable[[], NflverseData]):
        self._engine = engine
        self._make_data = nflverse

    def __call__(self, now: datetime | None = None) -> VerifySummary:
        now = now or _utcnow()
        out = VerifySummary()
        with Session(self._engine, expire_on_commit=False) as session:
            this_week = Game.commence_time >= now - VERIFY_WITHIN
            games = session.scalars(select(Game).where(
                Game.status == EventStatus.FINAL, Game.verified_at.is_(None),
                Game.espn_event_id.is_not(None), Game.final_at <= now - VERIFY_AFTER,
                this_week).order_by(Game.commence_time)).all()
            late = session.scalars(select(Game).where(
                Game.verified_at.is_not(None), Game.plays_checked_at.is_(None),
                Game.espn_event_id.is_not(None), this_week).order_by(Game.commence_time)).all()
            if not games and not late:
                return out  # nothing to do, so nothing is downloaded
            data = self._make_data()
            done: list[Game] = []
            try:
                for game in games:
                    if self._verify(session, data, game, now, out):
                        done.append(game)
                    session.commit()
            except (NflverseError, ProviderOpen) as e:
                session.rollback()
                log.warning("verify_games stopped: %s", e)
            if done or late:
                late_ids = {game.id for game in late}
                read = self._exact_plays(session, data, [*late, *done], now, out)
                out.late = len(read & late_ids)
        return out

    @staticmethod
    def _exact_plays(session: Session, data: NflverseData, games: list[Game], now: datetime,
                     out: VerifySummary) -> set[int]:
        """From each game's play-by-play: the play that decided each winning Over, and the
        checks of the longest plays and the first touchdown. One download per season. A game
        nflverse hasn't put in the play-by-play yet is left for a later run. Returns the ids of
        the games read."""
        by_season: dict[int, dict[str, Game]] = defaultdict(dict)
        read: set[int] = set()
        try:
            # Inside the try: on a run that only retries late games, this is the first download.
            for game in games:
                assert game.espn_event_id is not None
                season = nfl_season(game.commence_time)
                if (game_id := data.game_id(season, game.espn_event_id)) is not None:
                    by_season[season][game_id] = game
            for season, by_id in by_season.items():
                plays = data.plays(season, frozenset(by_id))
                for game_id, game in by_id.items():
                    rows = plays[game_id]
                    if not rows:
                        log.info("%s: no play-by-play yet; a later run reads it", game.label)
                        continue
                    out.plays += deciding.exact_plays(session, game, rows, data.gsis_id, now)
                    deciding.check_longest(session, game, rows, data.gsis_id, now)
                    deciding.check_first_td(game, rows, data.espn_id)
                    game.plays_checked_at = now
                    session.commit()
                    read.add(game.id)
        except (NflverseError, ProviderOpen) as e:
            session.rollback()
            log.warning("verify_games: no play-by-play this run: %s", e)
        return read

    def _verify(self, session: Session, data: NflverseData, game: Game, now: datetime,
                out: VerifySummary) -> bool:
        """Check one game. True once it is verified."""
        assert game.espn_event_id is not None
        season = nfl_season(game.commence_time)
        score = data.final_score(season, game.espn_event_id)
        lines = data.game_stats(season, game.espn_event_id) if score is not None else {}
        if score is None or not lines:
            out.waiting += 1
            return False
        if score != (game.home_score, game.away_score):
            game.last_error = (f"nflverse has the final as {score[0]}-{score[1]} (home-away), "
                               f"not {game.home_score}-{game.away_score}: not verified")
            log.error("%s: %s", game.label, game.last_error)
            out.disputed += 1
            return False

        held: dict[str, dict[Stat, LiveStat]] = defaultdict(dict)
        for row in session.scalars(select(LiveStat).where(LiveStat.game_id == game.id)):
            held[row.espn_athlete_id][row.stat] = row
        changed = 0
        # Everyone the live box score listed. Someone nflverse has no line for keeps the feed's
        # numbers, unverified.
        for athlete, rows in held.items():
            gsis = data.gsis_id(athlete)
            line = lines.get(gsis) if gsis else None
            if line is None:
                continue
            for stat, value in stat_values(line).items():
                changed += self._apply(session, game, athlete, stat, rows.get(stat), value, now)
        # Players with a line in nflverse whom the live box score never listed: they played.
        unmapped = []
        for gsis, line in lines.items():
            athlete = data.espn_id(gsis)
            if athlete is None:
                unmapped.append(gsis)
            elif athlete not in held:
                for stat, value in stat_values(line).items():
                    changed += self._apply(session, game, athlete, stat, None, value, now)
        if unmapped:
            log.warning("%s: nflverse lines with no ESPN id, not checked: %s", game.label,
                        ", ".join(sorted(unmapped)))
        game.verified_at = now
        if changed:
            game.corrected_at = now
            out.corrected += changed
        out.verified += 1
        return True

    @staticmethod
    def _apply(session: Session, game: Game, athlete: str, stat: Stat, row: LiveStat | None,
               value: Decimal, now: datetime) -> int:
        """Mark the feed's value verified, or replace it with nflverse's. Returns 1 if changed."""
        if row is not None and row.value == value:
            row.verified_at = now
            return 0
        old = row.value if row is not None else None
        session.add(StatCorrection(
            game_id=game.id, espn_athlete_id=athlete, stat=stat, old_value=old, new_value=value,
            source=DataSource.NFLVERSE, corrected_at=now))
        if row is None:
            session.add(LiveStat(game_id=game.id, espn_athlete_id=athlete, stat=stat, value=value,
                                 source=DataSource.NFLVERSE, updated_at=now, verified_at=now))
        else:
            row.value, row.source, row.updated_at, row.verified_at = (
                value, DataSource.NFLVERSE, now, now)
        log.warning("%s: %s %s corrected from %s to %s by nflverse", game.label, athlete, stat,
                    old, value)
        return 1
