"""The owners' own section (/me): the bet pages each of them opened, their game recaps, and how
earlier lookups of a player or team went. The identity slice of the tracker's Phase 3
(docs/plans/integrate-parlaytracker.md §7); the slip endpoints come later.

The site signs owners in with their own passcodes (TRACKER_USERS) and forwards the name as
`x-juju-user`, the same trust as `x-juju-verified`: the backend has no public address, and the
public proxy never forwards this header. Unset, every route here is a 404. Every response is
no-store: a recap lists prices across players, allowed only for the owner's own lookups
(docs/licensing.md).
"""
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from juju.api.common import SessionDep, respond
from juju.api.recaps import Recaps, Totals, game_state
from juju.api.views import game_view, parse_focus, the_play
from juju.config import get_settings
from juju.core.enums import EventStatus
from juju.core.markets import BY_KEY
from juju.core.models import Game, Player
from juju.core.parlay import parse_legs
from juju.tracker import saved

RECAP_LOOKUPS = 60   # the latest this many lookups of one game
HISTORY_GAMES = 10
GAMES_LISTED = 40
RECENT = timedelta(hours=36)  # the home page's "your recap" strip: games final this recently
RECENT_GAMES = 4


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def owner(x_juju_user: Annotated[str | None, Header()] = None) -> str:
    names = get_settings().tracker_names
    if not names:
        raise HTTPException(404, "Not found")
    if x_juju_user not in names:
        raise HTTPException(401, "Sign in first.")
    return x_juju_user


Owner = Annotated[str, Depends(owner)]
router = APIRouter(prefix="/api/me", dependencies=[Depends(owner)])


class LookupIn(BaseModel):
    """A bet page as it was opened: what its URL asked."""
    kind: Literal["player", "team", "parlay"]
    game_id: int
    target: str = Field(min_length=1, max_length=600)
    play: str | None = Field(None, max_length=20)
    market: str | None = Field(None, max_length=60)
    threshold: str | None = Field(None, max_length=10)
    yards: int | None = None
    expect: bool = False
    play_id: int | None = None


def _asked(session, game: Game, body: LookupIn) -> saved.Asked:
    """What to save, checked as the page itself would read it. Raises 404 for a player or team
    who isn't in this game, 422 for a parlay's legs that don't parse."""
    if body.kind == "parlay":
        try:
            legs = parse_legs(body.target)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        teams = (game.home_espn_id, game.away_espn_id)
        for leg in legs:
            if leg.athlete is not None:
                player = session.get(Player, leg.athlete)
                if player is None or player.team_espn_id not in teams:
                    raise HTTPException(404, "A leg names a player who isn't in this game")
            elif leg.team not in teams:
                raise HTTPException(404, "A leg names a team that isn't in this game")
        return saved.Asked("parlay", ",".join(leg.text for leg in legs))
    focus = parse_focus(body.play, body.market, body.threshold if body.kind == "player" else None,
                        body.expect and body.kind == "player",
                        body.yards if body.kind == "player" else None)
    market = focus.market_key if focus.market_key in BY_KEY else None
    if body.kind == "player":
        if not body.target.isdigit() or session.get(Player, body.target) is None:
            raise HTTPException(404, "No such player")
        shown, focus = the_play(session, game, body.play_id, focus, athlete=body.target)
    else:
        if body.target not in (game.home_espn_id, game.away_espn_id):
            raise HTTPException(404, "No such team in this game")
        shown, focus = the_play(session, game, body.play_id, focus, team=body.target)
    return saved.Asked(body.kind, body.target, focus.play.value if focus.play else None, market,
                       focus.threshold, focus.yards, focus.expect,
                       shown["id"] if shown else None)


@router.post("/lookups")
def remember(body: LookupIn, who: Owner, session: SessionDep) -> JSONResponse:
    """Save a bet page this owner opened (the site sends it once the page is on screen)."""
    game = session.get(Game, body.game_id)
    if game is None:
        raise HTTPException(404, "No such game")
    lookup_id, new = saved.remember(session, who, game.id, _asked(session, game, body),
                                    _utcnow())
    session.commit()
    return respond({"id": lookup_id, "new": new})


@router.delete("/lookups/{lookup_id}")
def forget(lookup_id: int, who: Owner, session: SessionDep) -> JSONResponse:
    if not saved.forget(session, who, lookup_id):
        raise HTTPException(404, "No such lookup")
    session.commit()
    return respond({"removed": True})


@router.get("/recaps")
def recaps(who: Owner, session: SessionDep, recent: bool = False) -> JSONResponse:
    """The games this owner looked something up in, latest kickoff first. `recent`: only games
    final in the last 36 hours, with their totals (the home page's strip)."""
    now = _utcnow()
    rows = saved.games(session, who, GAMES_LISTED)
    if recent:
        rows = [r for r in rows if r[0].status is EventStatus.FINAL
                and r[0].final_at is not None and r[0].final_at >= now - RECENT][:RECENT_GAMES]
    build = Recaps(session, now, get_settings().books)
    out = []
    for game, count, seen in rows:
        item = {"game": game_view(game, now), "state": game_state(game), "lookups": count,
                "last_seen_at": seen}
        if recent:
            item["totals"] = build.recap(
                game, saved.for_game(session, who, game.id, RECAP_LOOKUPS))["totals"]
        out.append(item)
    return respond({"games": out})


@router.get("/recaps/{game_id}")
def recap(game_id: int, who: Owner, session: SessionDep) -> JSONResponse:
    """One game's recap: every bet page this owner opened for it, each with the bets it was
    about and how they went, and the totals."""
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(404, "No such game")
    lookups = saved.for_game(session, who, game.id, RECAP_LOOKUPS)
    if not lookups:
        raise HTTPException(404, "Nothing looked up in this game")
    return respond(Recaps(session, _utcnow(), get_settings().books).recap(game, lookups))


@router.get("/history/{kind}/{target}")
def history(kind: Literal["player", "team"], target: str, before_game: int, who: Owner,
            session: SessionDep) -> JSONResponse:
    """How this owner's lookups of a player or team went in the games before this one: for the
    private box on that player's or team's page."""
    game = session.get(Game, before_game)
    if game is None:
        raise HTTPException(404, "No such game")
    now = _utcnow()
    build = Recaps(session, now, get_settings().books)
    totals = Totals()
    games = []
    name = None
    for past, lookups in saved.of_target(session, who, kind, target, game.commence_time,
                                         HISTORY_GAMES):
        entries = [build.entry(past, s, totals) for s in lookups]
        name = name or next((e["who"] for e in entries if e["who"]), None)
        bets, seen = [], set()
        for e in entries:
            for b in e["bets"]:
                if (b["key"], b["line"]) not in seen:
                    seen.add((b["key"], b["line"]))
                    bets.append(b)
        games.append({"game": game_view(past, now), "state": game_state(past), "bets": bets,
                      "unpriced": sum(e["unpriced"] for e in entries),
                      "lookup_ids": [s.id for s in lookups]})
    return respond({"kind": kind, "id": target, "name": name, "games": games,
                    "totals": totals.as_dict()})
