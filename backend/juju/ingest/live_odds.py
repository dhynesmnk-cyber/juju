"""Live odds on demand: the one place a user's request reaches The Odds API
(docs/plans/live-chat.md, docs/GOALS.md decision 14). Everything else in the API reads Postgres.

What keeps it from burning credits:
- Odds are cached for at most 60 seconds (`MAX_ODDS_TTL`: a setting can shorten it, never
  lengthen it). The market list for a game is cached for 5 minutes, the schedule (free) too.
- The cache key is the game and the market, not the player: the reply holds every player, so a
  second player in the same market inside the minute is free. Only the asked-for player's lines
  ever leave this module's callers; the reply itself is never returned or listed.
- Two people asking the same thing at once make one call: a lock per cache key, and a second
  look at the cache once the lock is held.
- Nothing prefetches. Every call here is made because someone asked.
- The credits left are The Odds API's own count (`x-requests-remaining`), never a sum of ours.
"""
import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from juju.core.livechat import Posting
from juju.core.markets import BY_KEY, Market, Scope
from juju.ingest.http import FetchError, RateLimited
from juju.ingest.odds_api import ApiEvent, EventMarkets, EventOdds, OddsResponse, RequestRejected
from juju.ingest.router import ProviderOpen

log = logging.getLogger("juju.live_odds")

MAX_ODDS_TTL = 60            # seconds: odds change quickly, so never longer (the owner's rule)
MARKETS_TTL = 300            # the markets a game offers
GAMES_TTL = 300              # the schedule (free)
MAX_ENTRIES = 400            # a sanity bound on the cache


class LiveError(Exception):
    """The live odds can't be had; `message` is safe to show."""

    def __init__(self, message: str, kind: str = "unavailable"):
        super().__init__(message)
        self.message = message
        self.kind = kind


class LiveSource(Protocol):
    """What this module needs of the Odds API client; tests supply a fake."""

    @property
    def quota_remaining(self) -> int | None: ...

    last_cost: int | None

    def events(self) -> list[ApiEvent]: ...

    def event_odds(self, event_id: str, markets: Sequence[str],
                   bookmakers: Sequence[str]) -> OddsResponse: ...

    def event_markets(self, event_id: str, bookmakers: Sequence[str]) -> EventMarkets: ...


@dataclass(frozen=True)
class Fetched[T]:
    value: T
    fetched_at: datetime      # when The Odds API said it (our clock, at the reply)
    cached: bool              # served from the cache: no credit was spent
    cost: int                 # credits this call spent (0 when cached)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class LiveOdds:
    def __init__(self, source: LiveSource, books: Sequence[str], *,
                 odds_ttl: int = MAX_ODDS_TTL, markets_ttl: int = MARKETS_TTL,
                 games_ttl: int = GAMES_TTL, clock: Callable[[], datetime] = _utcnow,
                 initial_remaining: int | None = None,
                 on_quota: Callable[[int, datetime], None] | None = None):
        self._source = source
        self._books = list(books)
        self.odds_ttl = timedelta(seconds=max(1, min(odds_ttl, MAX_ODDS_TTL)))
        self.markets_ttl = timedelta(seconds=max(1, markets_ttl))
        self.games_ttl = timedelta(seconds=max(1, games_ttl))
        self._clock = clock
        self._on_quota = on_quota
        self._remaining = initial_remaining
        self.spent = 0                      # credits this process has spent on live questions
        self._cache: dict[tuple, tuple[datetime, object]] = {}
        self._guard = threading.Lock()      # the cache dict and the per-key locks
        self._locks: defaultdict[tuple, threading.Lock] = defaultdict(threading.Lock)
        self._call = threading.Lock()       # one call at a time, so `last_cost` is ours

    @property
    def remaining(self) -> int | None:
        """The credits The Odds API last said it has left; None until it has said."""
        return self._remaining

    # --- what callers ask for ----------------------------------------------------------------

    def games(self) -> Fetched[list[ApiEvent]]:
        """The schedule. Free (0 credits), so it is cached only to be kind."""
        return self._fetch(("games",), self.games_ttl, self._games)

    def markets(self, event_id: str) -> Fetched[list[str]]:
        """The market keys some book offers for this game (1 credit)."""
        return self._fetch(("markets", event_id), self.markets_ttl,
                           lambda: self._market_keys(event_id))

    def odds(self, event_id: str, market_key: str) -> Fetched[EventOdds]:
        """This game's odds in one market, from every book in the chain (1 credit)."""
        return self._fetch(("odds", event_id, market_key), self.odds_ttl,
                           lambda: self._odds(event_id, market_key))

    def cached(self, event_id: str, market_key: str) -> bool:
        """Would `odds` be free right now? (For the cost shown before a question.)"""
        return self._peek(("odds", event_id, market_key), self.odds_ttl) is not None

    def markets_cached(self, event_id: str) -> bool:
        """Would `markets` be free right now?"""
        return self._peek(("markets", event_id), self.markets_ttl) is not None

    def peek(self, event_id: str, market_key: str) -> Fetched[EventOdds] | None:
        """This market's reply if the last minute fetched it; never a call, never a credit."""
        hit = self._peek(("odds", event_id, market_key), self.odds_ttl)
        return None if hit is None else Fetched(hit[1], hit[0], True, 0)  # type: ignore[arg-type]

    def cached_at(self, event_id: str, market_key: str) -> datetime | None:
        hit = self._peek(("odds", event_id, market_key), self.odds_ttl)
        return None if hit is None else hit[0]

    # --- the calls ---------------------------------------------------------------------------

    def _games(self) -> tuple[list[ApiEvent], int]:
        return self._source.events(), 0

    def _market_keys(self, event_id: str) -> tuple[list[str], int]:
        reply = self._source.event_markets(event_id, self._books)
        keys = {m.key for b in reply.bookmakers for m in b.markets}
        return sorted(keys), self._charged(1)

    def _odds(self, event_id: str, market_key: str) -> tuple[EventOdds, int]:
        response = self._source.event_odds(event_id, [market_key], self._books)
        return response.odds, self._charged(response.cost if response.cost is not None else 1)

    def _charged(self, assumed: int) -> int:
        """What this call cost: The Odds API's `x-requests-last` when it said, else `assumed`.
        Also takes the credits left, as it reports them."""
        cost = self._source.last_cost if self._source.last_cost is not None else assumed
        remaining = self._source.quota_remaining
        if remaining is not None:
            self._remaining = remaining
            if self._on_quota is not None:
                try:
                    self._on_quota(remaining, self._clock())
                except Exception:  # noqa: BLE001 - a failed write never fails the question
                    log.warning("could not record the credits left")
        self.spent += cost
        return cost

    # --- the cache ---------------------------------------------------------------------------

    def _peek(self, key: tuple, ttl: timedelta) -> tuple[datetime, object] | None:
        with self._guard:
            hit = self._cache.get(key)
        if hit is not None and self._clock() - hit[0] < ttl:
            return hit
        return None

    def _fetch[T](self, key: tuple, ttl: timedelta,
                  call: Callable[[], tuple[T, int]]) -> Fetched[T]:
        if (hit := self._peek(key, ttl)) is not None:
            return Fetched(hit[1], hit[0], True, 0)  # type: ignore[arg-type]
        with self._guard:
            lock = self._locks[key]
        with lock:
            # Someone may have fetched it while this question waited for the lock.
            if (hit := self._peek(key, ttl)) is not None:
                return Fetched(hit[1], hit[0], True, 0)  # type: ignore[arg-type]
            try:
                with self._call:
                    value, cost = call()
            except RequestRejected as e:
                raise _rejected(e) from e
            except ProviderOpen as e:
                raise LiveError("Live odds are paused for a moment: The Odds API is having "
                                "trouble. Try again shortly.") from e
            except RateLimited as e:
                raise LiveError("Too many live questions at once. Try again in a few "
                                "seconds.") from e
            except FetchError as e:
                log.warning("live odds fetch failed: %s", e.kind)
                raise LiveError("The Odds API didn't answer. Nothing was shown, and no "
                                "price was made up.") from e
            now = self._clock()
            with self._guard:
                self._cache[key] = (now, value)
                self._trim(now)
            return Fetched(value, now, False, cost)

    def _trim(self, now: datetime) -> None:
        """Drop what no ttl could still serve; called with the guard held."""
        oldest = max(self.odds_ttl, self.markets_ttl, self.games_ttl)
        for k in [k for k, (at, _) in self._cache.items() if now - at >= oldest]:
            del self._cache[k]
        while len(self._cache) > MAX_ENTRIES:
            del self._cache[min(self._cache, key=lambda k: self._cache[k][0])]


def _rejected(e: RequestRejected) -> LiveError:
    text = str(e)
    if e.status_code == 401 and "OUT_OF_USAGE_CREDITS" in text:
        return LiveError("Juju's Odds API credits for the month are used up, so there are no "
                         "live prices.", "out_of_credits")
    if e.status_code == 401:
        return LiveError("The Odds API didn't accept Juju's key, so there are no live prices.")
    return LiveError("The Odds API doesn't offer that market for this game.", "not_offered")


# --- Reading a reply ---------------------------------------------------------------------------


def _subject(market: Market, name: str, description: str | None) -> str | None:
    """Who an outcome is about: the player (or the team, for a team total) in `description`;
    for a spread or moneyline the outcome's own name is the team; a game total has none."""
    if market.scope in (Scope.PLAYER, Scope.TEAM):
        return description
    if market.scope in (Scope.SPREAD, Scope.MONEYLINE):
        return name
    return None


def market_offered(reply: EventOdds, market_key: str) -> bool:
    """Did any book post this market, with at least one outcome?"""
    return any(m.key == market_key and m.outcomes
               for b in reply.bookmakers for m in b.markets)


def subjects(reply: EventOdds, market_key: str) -> list[str]:
    """The players (or teams) a market names, to match what someone typed against. Names only,
    in the order first seen; never prices, and never returned to a person as a list of prices."""
    market = BY_KEY.get(market_key)
    if market is None:
        return []
    seen: dict[str, None] = {}
    for b in reply.bookmakers:
        for m in b.markets:
            if m.key != market_key:
                continue
            for o in m.outcomes:
                who = _subject(market, o.name, o.description)
                if who:
                    seen.setdefault(who)
    return list(seen)


def postings_for(reply: EventOdds, market_keys: Collection[str],
                 matches: Callable[[str | None], bool]) -> list[Posting]:
    """The outcomes of one subject (`matches` picks him) in these markets, from every book. The
    only way prices leave a reply, so the answer can never be wider than one player or team."""
    out: list[Posting] = []
    for b in reply.bookmakers:
        for m in b.markets:
            market = BY_KEY.get(m.key)
            if market is None or m.key not in market_keys:
                continue
            for o in m.outcomes:
                if not matches(_subject(market, o.name, o.description)):
                    continue
                point = None if o.point is None else Decimal(str(o.point))
                out.append(Posting(b.key, m.key, o.name, point, o.price, m.last_update))
    return out
