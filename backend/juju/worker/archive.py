"""Writing odds snapshots: every price with its provenance (docs/GOALS.md section 4).

A snapshot keeps the response exactly as received (gzipped), its sha256, when it was fetched
and which moment it represents. Player names are matched to the game's two rosters with
parlaytracker's rule; a name that doesn't match cleanly is recorded as doubtful or unmapped,
and its prices keep no player id rather than a guessed one.
"""
import gzip
import hashlib
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.core.enums import SnapshotSource
from juju.core.markets import BY_KEY, Scope
from juju.core.models import Game, OddsSnapshot, Player, PlayerNameMap, Price
from juju.ingest.odds_api import OddsResponse
from juju.ingest.resolve import RosterEntry, match_roster_player

log = logging.getLogger("juju.archive")


def roster_for(session: Session, game: Game) -> list[RosterEntry]:
    teams = [t for t in (game.home_espn_id, game.away_espn_id) if t]
    if not teams:
        return []
    rows = session.scalars(select(Player).where(Player.team_espn_id.in_(teams))).all()
    return [RosterEntry(p.espn_athlete_id, p.name, p.team_espn_id, p.unavailable) for p in rows]


class NameMapper:
    """Odds API player names -> ESPN athlete ids for one game, cached in `player_name_map`."""

    def __init__(self, session: Session, game: Game):
        self._session = session
        self._game = game
        self._known = {m.raw_name: m for m in session.scalars(
            select(PlayerNameMap).where(PlayerNameMap.game_id == game.id))}
        self._roster: list[RosterEntry] | None = None

    def athlete_id(self, raw_name: str) -> str | None:
        held = self._known.get(raw_name)
        if held is None or (held.status == "unmapped" and self._roster is None):
            if self._roster is None:
                self._roster = roster_for(self._session, self._game)
            match = match_roster_player(raw_name, self._roster)
            status = ("unmapped" if match.espn_athlete_id is None
                      else "doubtful" if match.doubtful else "auto")
            if held is None:
                held = PlayerNameMap(game_id=self._game.id, raw_name=raw_name)
                self._session.add(held)
                self._known[raw_name] = held
            held.espn_athlete_id, held.score, held.status = (
                match.espn_athlete_id, match.score or None, status)
            if status != "auto":
                log.warning("%s: %s player name %r (best %s, %.0f)", self._game.label, status,
                            raw_name, match.name, match.score)
        return held.espn_athlete_id if held.status == "auto" else None


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
