"""The bet pages each owner opened, for their game recaps and history (/me). Juju's own, not
ported. The only place `saved_lookups` is written. Like services.py: these flush, callers
commit.

What makes two lookups the same is what a recap would show for them: the page (player, team or
parlay), who or which legs, and what was asked of it (a market, a threshold, a kind of play).
For the rest (the play's length, the play tapped), the latest visit wins.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from juju.core.models import Game
from juju.tracker.models import SavedLookup

KINDS = ("player", "team", "parlay")


@dataclass(frozen=True)
class Asked:
    """A bet page as it was opened. `target` is an athlete id, a team id, or a parlay's legs as
    its URL writes them; `play` is a PlayKind value."""
    kind: str
    target: str
    play: str | None = None
    market_key: str | None = None
    threshold: Decimal | None = None
    yards: int | None = None
    expect: bool = False
    play_id: int | None = None


def signature(asked: Asked) -> str:
    target = asked.target
    if asked.kind == "parlay":
        target = ",".join(sorted(target.split(",")))  # the same legs in another order
    threshold = "" if asked.threshold is None else format(asked.threshold.normalize(), "f")
    return "|".join((asked.kind, target, asked.market_key or "", threshold, asked.play or ""))


def remember(session: Session, owner: str, game_id: int, asked: Asked,
             now: datetime) -> tuple[int, bool]:
    """Save a bet page an owner opened, or bump the one already saved: (its id, new)."""
    stmt = insert(SavedLookup).values(
        owner=owner, game_id=game_id, kind=asked.kind, target=asked.target, play=asked.play,
        market_key=asked.market_key, threshold=asked.threshold, yards=asked.yards,
        expect=asked.expect, play_id=asked.play_id, signature=signature(asked), times=1,
        first_seen_at=now, last_seen_at=now)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_saved_lookups_owner_game_signature",
        set_={"last_seen_at": now, "times": SavedLookup.times + 1,
              "target": stmt.excluded.target, "expect": stmt.excluded.expect,
              # A visit that names no play keeps the one already known.
              "play_id": func.coalesce(stmt.excluded.play_id, SavedLookup.play_id),
              "yards": func.coalesce(stmt.excluded.yards, SavedLookup.yards)},
    ).returning(SavedLookup.id, SavedLookup.times)
    row = session.execute(stmt).one()
    session.flush()
    return row.id, row.times == 1


def forget(session: Session, owner: str, lookup_id: int) -> bool:
    """Remove one of this owner's lookups. False when there is no such lookup of theirs."""
    gone = session.execute(delete(SavedLookup).where(
        SavedLookup.id == lookup_id, SavedLookup.owner == owner))
    session.flush()
    return gone.rowcount > 0


def for_game(session: Session, owner: str, game_id: int, limit: int) -> list[SavedLookup]:
    """An owner's lookups of one game, in the order they first opened them (the latest
    `limit`)."""
    rows = session.scalars(select(SavedLookup).where(
        SavedLookup.owner == owner, SavedLookup.game_id == game_id)
        .order_by(SavedLookup.first_seen_at.desc(), SavedLookup.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def games(session: Session, owner: str, limit: int) -> list[tuple[Game, int, datetime]]:
    """The games an owner looked something up in, latest kickoff first: (game, lookups, last
    seen)."""
    counted = (select(SavedLookup.game_id, func.count().label("n"),
                      func.max(SavedLookup.last_seen_at).label("seen"))
               .where(SavedLookup.owner == owner).group_by(SavedLookup.game_id).subquery())
    rows = session.execute(select(Game, counted.c.n, counted.c.seen)
                           .join(counted, counted.c.game_id == Game.id)
                           .order_by(Game.commence_time.desc()).limit(limit)).all()
    return [(g, n, seen) for g, n, seen in rows]


def of_target(session: Session, owner: str, kind: str, target: str, before: datetime,
              games_limit: int) -> list[tuple[Game, list[SavedLookup]]]:
    """An owner's lookups of one player or team in games that kicked off before `before`,
    grouped by game, latest game first, at most `games_limit` games."""
    rows = session.execute(select(Game, SavedLookup)
                           .join(SavedLookup, SavedLookup.game_id == Game.id)
                           .where(SavedLookup.owner == owner, SavedLookup.kind == kind,
                                  SavedLookup.target == target, Game.commence_time < before)
                           .order_by(Game.commence_time.desc(), SavedLookup.first_seen_at,
                                     SavedLookup.id)).all()
    out: list[tuple[Game, list[SavedLookup]]] = []
    for game, saved in rows:
        if not out or out[-1][0].id != game.id:
            if len(out) == games_limit:
                break
            out.append((game, []))
        out[-1][1].append(saved)
    return out
