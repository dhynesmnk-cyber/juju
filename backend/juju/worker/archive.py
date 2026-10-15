"""Writing odds snapshots: every price with its provenance (docs/GOALS.md section 4).

A snapshot keeps the response exactly as received (gzipped), its sha256, when it was fetched
and which moment it represents. Player names are matched to the game's two rosters with
parlaytracker's rule; a name that doesn't match cleanly is recorded as doubtful or unmapped,
and its prices keep no player id rather than a guessed one. Such a name is tried again with
each new snapshot and whenever the rosters change (`remap_prices`): a roster that arrives late
then links the prices already stored. Only which player a price belongs to changes, never the
price.
"""
import gzip
import hashlib
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, exists, or_, select, update
from sqlalchemy.orm import Session

from juju.core.enums import SnapshotSource
from juju.core.markets import BY_KEY, CATALOG, Scope
from juju.core.models import Game, OddsSnapshot, Player, PlayerNameMap, Price
from juju.ingest.odds_api import OddsResponse
from juju.ingest.resolve import RosterEntry, match_roster_player

log = logging.getLogger("juju.archive")

PLAYER_KEYS = tuple(m.key for m in CATALOG if m.scope is Scope.PLAYER)
REMAP_DAYS = 14  # how far back a roster change re-links stored prices (repair_gaps' default)


def roster_for(session: Session, game: Game) -> list[RosterEntry]:
    teams = [t for t in (game.home_espn_id, game.away_espn_id) if t]
    if not teams:
        return []
    rows = session.scalars(select(Player).where(Player.team_espn_id.in_(teams))).all()
    return [RosterEntry(p.espn_athlete_id, p.name, p.team_espn_id, p.unavailable) for p in rows]


# Outcomes in player markets that are not players (seen in the 2026-10-01 PIT @ CLE recording):
# a team's defense scoring the touchdown, and the first-TD market's "No Touchdown".
_NOT_A_PLAYER = re.compile(r"\b(d/st|dst|defense|defence)\s*$|^no (touchdown|td)\b", re.I)


def is_not_a_player(raw_name: str) -> bool:
    return bool(_NOT_A_PLAYER.search(raw_name.strip()))


class NameMapper:
    """Odds API player names -> ESPN athlete ids for one game, cached in `player_name_map`."""

    def __init__(self, session: Session, game: Game):
        self._session = session
        self._game = game
        self._known = {m.raw_name: m for m in session.scalars(
            select(PlayerNameMap).where(PlayerNameMap.game_id == game.id))}
        self._roster: list[RosterEntry] | None = None

    def athlete_id(self, raw_name: str) -> str | None:
        if is_not_a_player(raw_name):
            return None  # stored with no player, never matched to one
        held = self._known.get(raw_name)
        # A name that isn't matched cleanly yet is tried again: the roster may have arrived or
        # changed since. (Until 2026-10-15 only the first such name in a snapshot was.)
        if held is None or held.status != "auto":
            if self._roster is None:
                self._roster = roster_for(self._session, self._game)
            match = match_roster_player(raw_name, self._roster)
            status = ("unmapped" if match.espn_athlete_id is None
                      else "doubtful" if match.doubtful else "auto")
            changed = held is None or (held.espn_athlete_id, held.status) != (
                match.espn_athlete_id, status)
            if held is None:
                held = PlayerNameMap(game_id=self._game.id, raw_name=raw_name)
                self._session.add(held)
                self._known[raw_name] = held
            held.espn_athlete_id, held.score, held.status = (
                match.espn_athlete_id, match.score or None, status)
            if status != "auto" and changed:
                log.warning("%s: %s player name %r (best %s, %.0f)", self._game.label, status,
                            raw_name, match.name, match.score)
        return held.espn_athlete_id if held.status == "auto" else None


def remap_prices(session: Session, game: Game) -> int:
    """Match this game's unmatched and doubtful names again, against the rosters as they are
    now, and point the prices stored under a name that now matches cleanly at its player.
    Returns the prices linked. The caller commits."""
    pending = session.scalars(select(PlayerNameMap).where(
        PlayerNameMap.game_id == game.id, PlayerNameMap.status != "auto")).all()
    if not pending:
        return 0
    roster = roster_for(session, game)
    if not roster:
        return 0
    linked = 0
    for held in pending:
        match = match_roster_player(held.raw_name, roster)
        if match.espn_athlete_id is None or match.doubtful:
            continue
        held.espn_athlete_id, held.score, held.status = match.espn_athlete_id, match.score, "auto"
        count = session.execute(update(Price).where(
            Price.game_id == game.id, Price.description == held.raw_name,
            Price.market_key.in_(PLAYER_KEYS), Price.espn_athlete_id.is_(None))
            .values(espn_athlete_id=match.espn_athlete_id)).rowcount
        linked += count
        log.info("%s: player name %r now matches %s: %d prices linked", game.label,
                 held.raw_name, match.name, count)
    session.flush()
    return linked


def remap_recent(engine: Engine, now: datetime, teams: Iterable[str] | None = None) -> int:
    """`remap_prices` for every game of the last two weeks and the next day and a half with
    unmatched names, or only those of `teams`. Returns the prices linked."""
    linked = 0
    with Session(engine) as session:
        q = select(Game).where(
            Game.commence_time.between(now - timedelta(days=REMAP_DAYS), now + timedelta(hours=36)),
            exists().where(PlayerNameMap.game_id == Game.id, PlayerNameMap.status != "auto"))
        if teams is not None:
            wanted = list(teams)
            q = q.where(or_(Game.home_espn_id.in_(wanted), Game.away_espn_id.in_(wanted)))
        for game in session.scalars(q):
            linked += remap_prices(session, game)
        session.commit()
    return linked


def store_snapshot(session: Session, game: Game, response: OddsResponse, *,
                   source: SnapshotSource, slot: str, fetched_at: datetime,
                   markets: list[str], books: list[str]) -> OddsSnapshot:
    """Write one response and all its prices. The caller commits."""
    raw = response.raw.encode()
    observed_at = response.vendor_ts if source is SnapshotSource.HISTORICAL else fetched_at
    assert observed_at is not None
    snapshot = OddsSnapshot(
        game_id=game.id, source=source, slot=slot, observed_at=observed_at,
        fetched_at=fetched_at, vendor_ts=response.vendor_ts,
        commence_time=response.odds.commence_time, markets=",".join(markets),
        books=",".join(books), cost=response.cost,
        payload_sha256=hashlib.sha256(raw).hexdigest(), payload_gzip=gzip.compress(raw))
    session.add(snapshot)
    session.flush()
    mapper = NameMapper(session, game)
    count = 0
    for bookmaker in response.odds.bookmakers:
        for market in bookmaker.markets:
            spec = BY_KEY.get(market.key)
            if spec is None:
                continue
            for o in market.outcomes:
                if not (o.price >= 100 or o.price <= -100):
                    continue  # never store an impossible price
                athlete = None
                if spec.scope is Scope.PLAYER and o.description:
                    athlete = mapper.athlete_id(o.description)
                session.add(Price(
                    snapshot_id=snapshot.id, game_id=game.id, book=bookmaker.key,
                    market_key=market.key, market_last_update=market.last_update,
                    outcome_name=o.name, description=o.description, espn_athlete_id=athlete,
                    point=None if o.point is None else Decimal(str(o.point)), american=o.price))
                count += 1
    session.flush()
    log.info("%s: stored %s %s snapshot (%d prices, cost %s)", game.label, source, slot, count,
             response.cost)
    return snapshot
