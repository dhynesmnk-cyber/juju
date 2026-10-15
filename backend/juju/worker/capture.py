"""The archive jobs (docs/GOALS.md section 4): `capture_t45`, `repair_gaps` and `backfill`.

- `capture_t45` takes the main markets at T-3h, then every market at T-45 (the owner's choice
  on 2026-10-15; before, also at T-60, T-49 and T-47). The T-45 capture starts at T-46, so a
  failure can be retried every 20 s until T-45:00; nothing is captured after that: a price
  from after T-45 is never used. How much each game may spend is planned from the credits left
  (`core/budget.py`): when they are short, the T-3h check goes first, then markets.
- `repair_gaps` fills any game that reached T-44 without an on-time snapshot from the
  vendor's own archive, which returns the closest snapshot at or before the time asked for.
- An unknown market key gets the whole request rejected (422), so the market list is split in
  halves until the bad key is found; it is then dropped and reported, and the rest captured.
"""
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, exists, func, select
from sqlalchemy.orm import Session

from juju.core import budget
from juju.core.enums import SnapshotSource
from juju.core.markets import ALL_KEYS, MAIN_KEYS
from juju.core.models import Game, OddsSnapshot
from juju.core.t45 import ON_TIME_WITHIN, target_time
from juju.ingest.http import FetchError, RateLimited
from juju.ingest.odds_api import OddsResponse, OddsSource, RequestRejected
from juju.ingest.router import ProviderOpen
from juju.worker.archive import store_snapshot

log = logging.getLogger("juju.capture")

HISTORICAL_MULTIPLIER = 10
RETRY_EVERY = timedelta(seconds=20)
REPAIR_FROM = timedelta(minutes=44)   # before kickoff: one minute after T-45
REPAIR_RETRY = timedelta(hours=1)


@dataclass(frozen=True)
class Slot:
    name: str
    before_kickoff: timedelta
    markets: tuple[str, ...]
    priority: bool  # may spend below the reserve: this is the price the product is about


EARLY = Slot("t180", timedelta(hours=3), MAIN_KEYS, False)
T45 = Slot("t45", timedelta(minutes=46), ALL_KEYS, True)
SLOTS: tuple[Slot, ...] = (EARLY, T45)


def slots_for(plan: budget.Plan) -> tuple[Slot, ...]:
    """The slots a game's plan pays for, with the markets its T-45 capture asks for."""
    if plan.tier is budget.Tier.NONE:
        return ()
    t45 = Slot(T45.name, T45.before_kickoff, plan.markets, True)
    return (EARLY, t45) if plan.early else (t45,)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def due_slot(kickoff: datetime, now: datetime, done: set[str],
             slots: Sequence[Slot] = SLOTS) -> Slot | None:
    """The slot to capture now, if any: the latest one whose time has come, unless it is
    already captured. Earlier slots missed (say, after a restart) are skipped, not replayed."""
    if now > target_time(kickoff):
        return None
    due = [s for s in slots if now >= kickoff - s.before_kickoff]
    if not due or due[-1].name in done:
        return None
    return due[-1]


def affordable(quota: int | None, cost: int, reserve: int, priority: bool) -> bool:
    """An unknown quota (before the first response) is allowed through."""
    if quota is None:
        return True
    return quota - cost >= (0 if priority else reserve)


class Rejections:
    """Market keys the vendor refused, remembered for the life of the process."""

    def __init__(self) -> None:
        self.keys: set[str] = set()

    def usable(self, markets: Sequence[str]) -> list[str]:
        return [m for m in markets if m not in self.keys]


def fetch_bisecting(call: Callable[[list[str]], OddsResponse], markets: list[str],
                    rejections: Rejections) -> list[tuple[list[str], OddsResponse]]:
    """Call with `markets`; on a 422, split the list until each bad key is isolated. Returns
    every successful (markets, response) pair. A rejected request costs nothing."""
    try:
        return [(markets, call(markets))]
    except RequestRejected as e:
        if e.status_code != 422:
            raise
        if len(markets) == 1:
            rejections.keys.add(markets[0])
            log.error("The Odds API rejected market %r: dropped until restart (%s)", markets[0], e)
            return []
    half = len(markets) // 2
    return (fetch_bisecting(call, markets[:half], rejections)
            + fetch_bisecting(call, markets[half:], rejections))


def games_before(session: Session, now: datetime, until: datetime) -> int:
    """Games the vendor lists that kick off after `now` and by `until`."""
    return session.scalar(select(func.count()).select_from(Game).where(
        Game.odds_event_id.is_not(None), Game.commence_time > now,
        Game.commence_time <= until)) or 0


def current_plan(session: Session, quota: int | None, now: datetime, reset_day: int,
                 games_per_week: int) -> budget.Plan:
    """The plan every game captured now follows, from the credits left and the games left."""
    reset_at = budget.next_reset(now, reset_day)
    left = budget.games_left(games_before(session, now, reset_at), now, reset_at, games_per_week)
    return budget.plan(quota, left)


class CaptureT45:
    def __init__(self, engine: Engine, odds: OddsSource, books: Sequence[str], reserve: int,
                 clock: Callable[[], datetime] = _utcnow, reset_day: int = 1,
                 games_per_week: int = 16, before: Callable[[Session, Game], object] | None = None):
        self._engine = engine
        self._odds = odds
        self._books = list(books)
        self._reserve = reserve
        self._clock = clock
        self._reset_day = reset_day
        self._games_per_week = games_per_week
        self._before = before  # run before each capture, e.g. re-matching player names
        self._attempted: dict[tuple[int, str], datetime] = {}
        self.rejections = Rejections()

    def __call__(self) -> int:
        """Capture every slot that is due. Returns the number of snapshots stored."""
        now = self._clock()
        stored = 0
        with Session(self._engine, expire_on_commit=False) as session:
            games = session.scalars(
                select(Game).where(Game.odds_event_id.is_not(None),
                                   Game.commence_time > now,
                                   Game.commence_time <= now + EARLY.before_kickoff)
                .order_by(Game.commence_time, Game.id)).all()
            if not games:
                return 0
            plan = current_plan(session, self._odds.quota_remaining, now, self._reset_day,
                                self._games_per_week)
            for game in games:
                self._record(session, game, plan)
                done = set(session.scalars(select(OddsSnapshot.slot).where(
                    OddsSnapshot.game_id == game.id,
                    OddsSnapshot.source == SnapshotSource.LIVE)))
                slot = due_slot(game.commence_time, now, done, slots_for(plan))
                if slot is None:
                    continue
                last = self._attempted.get((game.id, slot.name))
                if last is not None and now - last < RETRY_EVERY:
                    continue
                self._attempted[(game.id, slot.name)] = now
                if self._before is not None:
                    self._before(session, game)
                stored += self._capture(session, game, slot, now)
            session.commit()  # the plans recorded
        return stored

    @staticmethod
    def _record(session: Session, game: Game, plan: budget.Plan) -> None:
        """Keep the plan on the game, so its cards can say why a price is missing. A game that
        already has its T-45 capture keeps the plan it was captured under."""
        if game.capture_plan == plan.tier.value or session.scalar(select(exists().where(
                OddsSnapshot.game_id == game.id, OddsSnapshot.slot == T45.name))):
            return
        log.info("%s: capture plan %s (%s credits a game)", game.label, plan.tier, plan.per_game)
        game.capture_plan, game.capture_note = plan.tier.value, plan.note

    def _capture(self, session: Session, game: Game, slot: Slot, now: datetime) -> int:
        markets = self.rejections.usable(slot.markets)
        if not affordable(self._odds.quota_remaining, len(markets), self._reserve,
                          slot.priority):
            log.error("%s: not enough Odds API credits for %s (%s left)", game.label, slot.name,
                      self._odds.quota_remaining)
            return 0
        assert game.odds_event_id is not None
        event_id = game.odds_event_id
        try:
            results = fetch_bisecting(
                lambda m: self._odds.event_odds(event_id, m, self._books), markets,
                self.rejections)
        except (FetchError, RateLimited, ProviderOpen, RequestRejected) as e:
            log.warning("%s: %s capture failed, will retry: %s", game.label, slot.name, e)
            return 0
        for used, response in results:
            store_snapshot(session, game, response, source=SnapshotSource.LIVE, slot=slot.name,
                           fetched_at=self._clock(), markets=used, books=self._books)
            if response.odds.commence_time != game.commence_time:
                log.info("%s: kickoff moved to %s", game.label, response.odds.commence_time)
                game.commence_time = response.odds.commence_time
        session.commit()
        return len(results)


def has_on_time_snapshot(session: Session, game: Game) -> bool:
    target = target_time(game.commence_time)
    return bool(session.scalar(select(exists().where(
        OddsSnapshot.game_id == game.id,
        OddsSnapshot.observed_at <= target,
        OddsSnapshot.observed_at >= target - ON_TIME_WITHIN,
        OddsSnapshot.markets.contains("player_")))))


class RepairGaps:
    """Fill games with no on-time snapshot from the vendor's history (10x the credits)."""

    def __init__(self, engine: Engine, odds: OddsSource, books: Sequence[str], reserve: int,
                 repair_days: int, clock: Callable[[], datetime] = _utcnow):
        self._engine = engine
        self._odds = odds
        self._books = list(books)
        self._reserve = reserve
        self._days = repair_days
        self._clock = clock
        self._attempted: dict[int, datetime] = {}
        self.rejections = Rejections()

    def __call__(self, game_ids: Sequence[int] | None = None) -> int:
        now = self._clock()
        repaired = 0
        with Session(self._engine, expire_on_commit=False) as session:
            q = select(Game).where(Game.odds_event_id.is_not(None),
                                   Game.commence_time <= now + REPAIR_FROM)
            if game_ids is None:
                q = q.where(Game.commence_time >= now - timedelta(days=self._days))
            else:
                q = q.where(Game.id.in_(game_ids))
            for game in session.scalars(q.order_by(Game.commence_time)).all():
                if has_on_time_snapshot(session, game) or self._repaired(session, game):
                    continue
                last = self._attempted.get(game.id)
                if game_ids is None and last is not None and now - last < REPAIR_RETRY:
                    continue
                self._attempted[game.id] = now
                repaired += self._repair(session, game, now)
        return repaired

    @staticmethod
    def _repaired(session: Session, game: Game) -> bool:
        return bool(session.scalar(select(exists().where(
            OddsSnapshot.game_id == game.id, OddsSnapshot.slot == "repair"))))

    def _repair(self, session: Session, game: Game, now: datetime) -> int:
        markets = self.rejections.usable(ALL_KEYS)
        cost = HISTORICAL_MULTIPLIER * len(markets)
        if not affordable(self._odds.quota_remaining, cost, self._reserve, priority=False):
            log.error("%s: not enough Odds API credits to repair (%s left)", game.label,
                      self._odds.quota_remaining)
            return 0
        assert game.odds_event_id is not None
        event_id, at = game.odds_event_id, target_time(game.commence_time)
        try:
            results = fetch_bisecting(
                lambda m: self._odds.historical_event_odds(event_id, at, m, self._books),
                list(markets), self.rejections)
        except (FetchError, RateLimited, ProviderOpen, RequestRejected) as e:
            log.warning("%s: repair failed, will retry within the hour: %s", game.label, e)
            return 0
        for used, response in results:
            store_snapshot(session, game, response, source=SnapshotSource.HISTORICAL,
                           slot="repair", fetched_at=now, markets=used, books=self._books)
        session.commit()
        spent = sum(r.cost or 0 for _, r in results)
        log.info("%s: repaired from the vendor's history (%s credits, %s left)", game.label,
                 spent, self._odds.quota_remaining)
        return 1 if results else 0
