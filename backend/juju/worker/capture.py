"""The archive jobs (docs/GOALS.md section 4): `capture_t45`, `repair_gaps` and `backfill`.

- `capture_t45` takes a coverage snapshot at T-3h, then every market at T-60, T-49, T-47 and
  T-45:30. A failed slot is retried every 20 s until T-45:00, and nothing is captured after
  that: a price from after T-45 is never used.
- `repair_gaps` fills any game that reached T-44 without an on-time snapshot from the
  vendor's own archive, which returns the closest snapshot at or before the time asked for.
- An unknown market key gets the whole request rejected (422), so the market list is split in
  halves until the bad key is found; it is then dropped and reported, and the rest captured.
"""
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, exists, select
from sqlalchemy.orm import Session

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


SLOTS: tuple[Slot, ...] = (
    Slot("t180", timedelta(hours=3), MAIN_KEYS, False),
    Slot("t60", timedelta(minutes=60), ALL_KEYS, False),
    Slot("t49", timedelta(minutes=49), ALL_KEYS, True),
    Slot("t47", timedelta(minutes=47), ALL_KEYS, True),
    Slot("t45", timedelta(minutes=45, seconds=30), ALL_KEYS, True),
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def due_slot(kickoff: datetime, now: datetime, done: set[str]) -> Slot | None:
    """The slot to capture now, if any: the latest one whose time has come, unless it is
    already captured. Earlier slots missed (say, after a restart) are skipped, not replayed."""
    if now > target_time(kickoff):
        return None
    due = [s for s in SLOTS if now >= kickoff - s.before_kickoff]
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


class CaptureT45:
    def __init__(self, engine: Engine, odds: OddsSource, books: Sequence[str], reserve: int,
                 clock: Callable[[], datetime] = _utcnow):
        self._engine = engine
        self._odds = odds
        self._books = list(books)
        self._reserve = reserve
        self._clock = clock
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
                                   Game.commence_time <= now + SLOTS[0].before_kickoff)
                .order_by(Game.commence_time, Game.id)).all()
            for game in games:
                done = set(session.scalars(select(OddsSnapshot.slot).where(
                    OddsSnapshot.game_id == game.id,
                    OddsSnapshot.source == SnapshotSource.LIVE)))
                slot = due_slot(game.commence_time, now, done)
                if slot is None:
                    continue
                last = self._attempted.get((game.id, slot.name))
                if last is not None and now - last < RETRY_EVERY:
                    continue
                self._attempted[(game.id, slot.name)] = now
                stored += self._capture(session, game, slot, now)
        return stored

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
        log.info("%s: repaired from the vendor's history", game.label)
        return 1 if results else 0
