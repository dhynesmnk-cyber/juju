"""The live loop (docs/GOALS.md section 6): game state, box scores and notable plays.

After parlaytracker@c3bd43c parlaytracker/worker/live.py, which it follows on the rules:
- the integrity guards: a stale reading is discarded, a final never goes back to play, and a
  stopped clock is probed on the other host before a provider is blamed;
- ESPN's etiquette: the shared limiter caps ESPN at 20 requests a minute, and a request over
  the limit is skipped until the next tick, never queued.

Juju differs in what it reads, because anyone can look up any player at any moment:
- the scoreboard every 15 s while a game is in play (one request covers the day);
- box scores for *every* live game, in priority order: a game whose score just changed (the
  play everyone is about to look up), then games people are looking up now, then round-robin
  so no live game goes more than a minute without one;
- The Odds API's `/scores` as an independent source for game state when ESPN can't be reached.

Nothing here settles a bet. Cards decide outcomes from what is stored (`core/card.py`).
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from juju.core.enums import DataSource, EventStatus, PlayKind, Stat
from juju.core.models import Game, HotGame, LiveStat, Play, Player, StatCorrection
from juju.ingest import espn, guards
from juju.ingest.http import FetchError, RateLimited
from juju.ingest.odds_api import OddsSource, RequestRejected
from juju.ingest.resolve import (
    RosterEntry, match_abbreviated, match_roster_player, normalize_name, same_game,
)
from juju.ingest.router import AllProvidersFailed, EspnRouter, ProviderOpen, other_score_provider

log = logging.getLogger("juju.live")

ACTIVE_BEFORE = timedelta(minutes=30)
ACTIVE_AFTER = timedelta(hours=8)
SCOREBOARD_IN_PLAY = timedelta(seconds=15)
SCOREBOARD_PREGAME = timedelta(minutes=5)
BOX_HOT = timedelta(seconds=20)       # a game people are looking up
BOX_EVERY = timedelta(seconds=60)     # every other live game
HOT_FOR = timedelta(minutes=5)
ODDS_SCORES_EVERY = timedelta(seconds=60)
ODDS_SCORES_WHEN_ESPN_DOWN = timedelta(seconds=30)
ESPN_STALE = timedelta(seconds=90)    # older than this, The Odds API's scores are used
RECHECK_AFTER_FINAL = timedelta(minutes=10)
IN_PLAY = (EventStatus.IN_PROGRESS, EventStatus.BREAK, EventStatus.DELAYED)
_OVER = (EventStatus.FINAL, EventStatus.POSTPONED, EventStatus.CANCELLED)
_ESPN_ERRORS = (AllProvidersFailed, RateLimited, FetchError)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _key(game: Game) -> guards.ProgressKey | None:
    return guards.progress_key(espn.Sport.NFL, game.status, game.period, game.clock_seconds)


@dataclass
class LiveSummary:
    scoreboards: int = 0
    boxes: int = 0
    odds_scores: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class PollLive:
    def __init__(self, engine: Engine, router: EspnRouter, odds: OddsSource | None = None,
                 clock: Callable[[], datetime] = _utcnow):
        self._engine = engine
        self._router = router
        self._odds = odds
        self._clock = clock
        # In memory: a restart just makes everything due once, which is what we want.
        self._scoreboard_at: dict[date, datetime] = {}
        self._probe_at: dict[date, datetime] = {}
        self._odds_at: datetime | None = None
        self._score_changed: set[int] = set()

    def __call__(self) -> LiveSummary:
        now = self._clock()
        out = LiveSummary()
        with Session(self._engine, expire_on_commit=False) as session:
            games = self._active(session, now)
            if not games:
                return out
            by_day: dict[date, list[Game]] = {}
            for g in games:
                by_day.setdefault(espn.game_day(g.commence_time), []).append(g)
            for day, group in sorted(by_day.items()):
                self._scoreboard(session, day, group, now, out)
            self._odds_scores(session, games, now, out)
            for game in self._box_order(session, games, now):
                if not self._box(session, game, now, out):
                    break  # over ESPN's limit: the rest wait for the next tick
        return out

    # --- which games -------------------------------------------------------------------------

    @staticmethod
    def _active(session: Session, now: datetime) -> list[Game]:
        rows = session.scalars(select(Game).where(
            Game.espn_event_id.is_not(None),
            Game.commence_time.between(now - ACTIVE_AFTER, now + ACTIVE_BEFORE))).all()
        return [g for g in rows if g.status not in _OVER or _needs_recheck(g, now)]

    def _box_order(self, session: Session, games: list[Game], now: datetime) -> list[Game]:
        hot = set(session.scalars(select(HotGame.game_id).where(
            HotGame.last_lookup_at >= now - HOT_FOR)))
        due: list[tuple[int, datetime, Game]] = []
        for g in games:
            last = g.last_box_at
            if g.status is EventStatus.FINAL:
                if _needs_recheck(g, now):
                    due.append((0, g.commence_time, g))
                continue
            if g.status not in IN_PLAY:
                continue
            if g.id in self._score_changed:
                due.append((0, g.commence_time, g))
            elif g.id in hot and (last is None or now - last >= BOX_HOT):
                due.append((1, last or g.commence_time, g))
            elif last is None or now - last >= BOX_EVERY:
                due.append((2, last or g.commence_time, g))
        return [g for _, _, g in sorted(due, key=lambda t: (t[0], t[1]))]

    # --- scoreboard --------------------------------------------------------------------------

    def _scoreboard(self, session: Session, day: date, group: list[Game], now: datetime,
                    out: LiveSummary) -> None:
        in_play = any(g.status in IN_PLAY or g.commence_time <= now for g in group)
        every = SCOREBOARD_IN_PLAY if in_play else SCOREBOARD_PREGAME
        last = self._scoreboard_at.get(day)
        if last is not None and now - last < every:
            return
        try:
            routed = self._router.scoreboard(day)
        except _ESPN_ERRORS as e:
            out.errors.append(f"scoreboard {day}: {e}")
            return
        self._scoreboard_at[day] = now
        out.scoreboards += 1
        seen = {g.espn_event_id: g for g in routed.value.games}
        for game in group:
            reading = seen.get(game.espn_event_id)
            if reading is not None:
                self._apply(game, reading.status, reading.period, reading.clock_seconds,
                            reading.home_score, reading.away_score, routed.provider, now)
        session.commit()
        self._probe(session, day, group, routed.provider, now)

    def _probe(self, session: Session, day: date, group: list[Game], provider: DataSource,
               now: datetime) -> None:
        """A game whose clock hasn't moved for 5 minutes: ask the other host once. If it is
        ahead, use it; if not, the game itself is stopped (a review, an injury)."""
        frozen = [g for g in group if guards.is_frozen(g.status, g.last_progress_at, now)]
        if not frozen or not guards.may_probe(self._probe_at.get(day), now):
            return
        self._probe_at[day] = now
        other = other_score_provider(provider)
        try:
            routed = self._router.scoreboard(day, only=other)
        except _ESPN_ERRORS:
            return
        seen = {g.espn_event_id: g for g in routed.value.games}
        ahead = False
        for game in frozen:
            r = seen.get(game.espn_event_id)
            if r is None:
                continue
            new = guards.progress_key(espn.Sport.NFL, r.status, r.period, r.clock_seconds)
            if new is not None and guards.compare(new, _key(game)) is guards.Verdict.ADVANCE:
                ahead = True
        if ahead:
            from juju.core.enums import FailureKind
            self._router.breakers.failure(provider.value, FailureKind.FROZEN,
                                          f"{other.value} is ahead of a frozen feed")
            for game in group:
                if (r := seen.get(game.espn_event_id)) is not None:
                    self._apply(game, r.status, r.period, r.clock_seconds, r.home_score,
                                r.away_score, other, now)
            session.commit()
        else:
            log.info("%s: clock stopped, not frozen", ", ".join(g.label for g in frozen))

    def _apply(self, game: Game, status: EventStatus, period: int | None,
               clock_seconds: int | None, home: int | None, away: int | None,
               provider: DataSource, now: datetime) -> None:
        if not guards.accept_status_change(game.status, status):
            return
        new = guards.progress_key(espn.Sport.NFL, status, period, clock_seconds)
        held = _key(game)
        if new is not None and held is not None:
            verdict = guards.compare(new, held)
            if verdict is guards.Verdict.STALE:
                return
            if verdict is guards.Verdict.ADVANCE:
                game.last_progress_at = now
        elif new is not None:
            game.last_progress_at = now
        if (home, away) != (game.home_score, game.away_score) and home is not None:
            if game.home_score is not None:  # a change during the game: a play just happened
                self._score_changed.add(game.id)
            game.home_score, game.away_score = home, away
        if status is EventStatus.FINAL and game.status is not EventStatus.FINAL:
            game.final_at = now
        game.status, game.period, game.clock_seconds = status, period, clock_seconds
        game.last_polled_at, game.live_source = now, provider

    # --- The Odds API scores (licensed, independent) ------------------------------------------

    def _odds_scores(self, session: Session, games: list[Game], now: datetime,
                     out: LiveSummary) -> None:
        if self._odds is None or not any(g.commence_time <= now for g in games):
            return
        espn_down = all(self._router.breakers[s].is_open
                        for s in ("espn_web", "espn_site"))
        every = ODDS_SCORES_WHEN_ESPN_DOWN if espn_down else ODDS_SCORES_EVERY
        if self._odds_at is not None and now - self._odds_at < every:
            return
        self._odds_at = now
        try:
            scores = self._odds.scores()
        except (FetchError, RateLimited, ProviderOpen, RequestRejected) as e:
            out.errors.append(f"odds scores: {e}")
            return
        out.odds_scores += 1
        for game in games:
            if game.last_polled_at is not None and now - game.last_polled_at < ESPN_STALE:
                continue  # ESPN is current: it also has the clock
            s = next((x for x in scores if x.id == game.odds_event_id or same_game(
                game.home_team, game.away_team, game.commence_time, x.home_team, x.away_team,
                x.commence_time)), None)
            if s is None or not s.scores:
                continue
            pts = {normalize_name(x.name): x.score for x in s.scores}
            try:
                home = int(pts[normalize_name(game.home_team)])
                away = int(pts[normalize_name(game.away_team)])
            except (KeyError, ValueError):
                continue
            status = EventStatus.FINAL if s.completed else (
                game.status if game.status in IN_PLAY else EventStatus.IN_PROGRESS)
            if not guards.accept_status_change(game.status, status):
                continue
            if (home, away) != (game.home_score, game.away_score):
                self._score_changed.add(game.id)
                game.home_score, game.away_score = home, away
            if status is EventStatus.FINAL and game.status is not EventStatus.FINAL:
                game.final_at = now
            game.status, game.last_polled_at, game.live_source = status, now, DataSource.ODDS_API
        session.commit()

    # --- box scores and plays ----------------------------------------------------------------

    def _box(self, session: Session, game: Game, now: datetime, out: LiveSummary) -> bool:
        """Read one game's summary. False when ESPN's rate limit says stop for this tick."""
        assert game.espn_event_id is not None
        try:
            routed = self._router.summary(game.espn_event_id)
        except RateLimited:
            out.skipped += 1
            return False
        except (AllProvidersFailed, FetchError) as e:
            out.errors.append(f"{game.label}: {e}")
            return True
        self._score_changed.discard(game.id)
        box, raw_plays = routed.value.box, routed.value.plays
        write_box(session, game, box, routed.provider, now)
        write_plays(session, game, raw_plays)
        self._apply(game, box.status, game.period, game.clock_seconds, box.home_score,
                    box.away_score, routed.provider, now)
        self._score_changed.discard(game.id)  # the box score just covered that change
        game.last_box_at = now
        session.commit()
        out.boxes += 1
        return True


def _needs_recheck(game: Game, now: datetime) -> bool:
    """One more box score once a final game has settled, to catch late corrections."""
    return (game.status is EventStatus.FINAL and game.final_at is not None
            and now >= game.final_at + RECHECK_AFTER_FINAL
            and (game.last_box_at is None or game.last_box_at < game.final_at
                 + RECHECK_AFTER_FINAL) and now - game.final_at < timedelta(hours=6))


def write_box(session: Session, game: Game, box: espn.BoxScore, provider: DataSource,
              now: datetime) -> int:
    """Every stat for everyone who appears in the box score (a missing stat is then a real
    zero). Players who appear nowhere get no rows: "no stat line yet". Returns rows changed.
    A value that changes after the game has settled is recorded in `stat_corrections` and marks
    the game corrected. A value the next-day check took from nflverse is never overwritten."""
    held = {(r.espn_athlete_id, r.stat): r for r in session.scalars(
        select(LiveStat).where(LiveStat.game_id == game.id))}
    settled = (game.status is EventStatus.FINAL and game.final_at is not None
               and now >= game.final_at + RECHECK_AFTER_FINAL)
    changed = 0
    for athlete in box.appeared:
        for stat in Stat:
            value = box.stats.get(stat, {}).get(athlete, Decimal(0))
            row = held.get((athlete, stat))
            if row is not None and (row.value == value or row.source is DataSource.NFLVERSE):
                continue
            old = row.value if row is not None else None
            if settled:
                game.corrected_at = now
                session.add(StatCorrection(
                    game_id=game.id, espn_athlete_id=athlete, stat=stat, old_value=old,
                    new_value=value, source=provider, corrected_at=now))
                log.warning("%s: %s %s corrected from %s to %s after settling", game.label,
                            athlete, stat, old, value)
            session.execute(insert(LiveStat).values(
                game_id=game.id, espn_athlete_id=athlete, stat=stat, value=value,
                source=provider, updated_at=now).on_conflict_do_update(
                index_elements=[LiveStat.game_id, LiveStat.espn_athlete_id, LiveStat.stat],
                set_={"value": value, "source": provider, "updated_at": now,
                      "verified_at": None}))  # a new value hasn't been checked
            changed += 1
    return changed


def _team_roster(session: Session, team: str | None) -> list[RosterEntry]:
    if not team:
        return []
    return [RosterEntry(p.espn_athlete_id, p.name, p.team_espn_id, p.unavailable)
            for p in session.scalars(select(Player).where(Player.team_espn_id == team))]


def play_label(name: str | None, raw: espn.RawPlay) -> str:
    who = "Team"
    if name:
        first, _, last = name.partition(" ")
        who = f"{first[:1]}. {last}" if last else name
    yards = f"{raw.yards}-yd " if raw.yards is not None else ""
    if raw.kind is PlayKind.TOUCHDOWN:
        what = "TD catch" if raw.passer_name else "TD run" if "rush" in raw.text.lower() or \
            " up the middle" in raw.text.lower() or " left " in raw.text.lower() or \
            " right " in raw.text.lower() else "TD"
    else:
        what = {PlayKind.RUN: "run", PlayKind.CATCH: "catch",
                PlayKind.FIELD_GOAL: "FG"}.get(raw.kind, raw.kind.value)
    return f"{who} {yards}{what}"[:80]


def write_plays(session: Session, game: Game, raws: list[espn.RawPlay]) -> int:
    """Store new notable plays, with the player matched to the team's roster when that is
    certain. An unmatched play keeps no player (it can still be shown as a team play)."""
    known = set(session.scalars(select(Play.espn_play_id).where(Play.game_id == game.id)))
    rosters: dict[str | None, list[RosterEntry]] = {}
    names = {}
    added = 0
    for raw in raws:
        if raw.espn_play_id in known:
            continue
        roster = rosters.setdefault(raw.team_espn_id, _team_roster(session, raw.team_espn_id))
        athlete = None
        if raw.player_name:
            if "." in raw.player_name.split(" ")[0]:
                athlete = match_abbreviated(raw.player_name, roster)
            else:
                m = match_roster_player(raw.player_name, roster)
                athlete = m.espn_athlete_id if m.espn_athlete_id and not m.doubtful else None
        if athlete and athlete not in names:
            player = session.get(Player, athlete)
            names[athlete] = player.name if player else None
        session.add(Play(
            game_id=game.id, espn_play_id=raw.espn_play_id, sequence=raw.sequence,
            period=raw.period, clock=raw.clock, wallclock=raw.wallclock, kind=raw.kind.value,
            label=play_label(names.get(athlete) if athlete else None, raw), yards=raw.yards,
            text=raw.text[:500], espn_athlete_id=athlete, team_espn_id=raw.team_espn_id,
            scoring=raw.scoring))
        added += 1
    session.flush()
    return added
