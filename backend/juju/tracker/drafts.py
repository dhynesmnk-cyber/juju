# Ported from parlaytracker@c3bd43c parlaytracker/app/components/slip_form.py: what its form worked
# out from what was typed (`_read_leg`, `_slip_type`, `_computed_parlay_odds`, `_slip_odds`,
# `_build`, `_messages`, `_save`'s event step and `_live_warnings`), without the widgets. Changes:
# a draft arrives as one request body instead of Streamlit session state; the game and the
# player's name are looked up here from the ids the form sends (and a player not on either
# roster is refused); and saving and previewing share `draft_slip`.
"""From a filled-in slip form to a validated `SlipIn` (SPEC.md sections 5 and 9.3).

The site sends what the person chose: games and players by ESPN id, lines and odds as typed.
Everything else is worked out here, as parlaytracker's form did: the slip type (one leg is a
single; legs in one game a same-game parlay unless the person says otherwise), a parlay's odds
from its legs when the slip's are left blank, and the non-blocking warnings (odds that don't
match, a payout that doesn't, a selection already logged, a game already started).
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from juju.core.odds import american_odds, parlay_decimal
from juju.ingest import espn
from juju.tracker import services
from juju.tracker.models import EntrySource, MarketType, SlipType, Sport, TeamSide
from juju.tracker.pickers import Pickers
from juju.tracker.schemas import PLAYER_MARKETS, TEAM_MARKETS, SlipIn

ESPN_ID = r"^\d{1,20}$"


class LegDraft(BaseModel):
    sport: Sport = Sport.NFL
    day: date  # the game day (US Eastern) the game was picked from
    espn_event_id: str | None = Field(default=None, pattern=ESPN_ID)
    market_type: MarketType = MarketType.GAME_TOTAL
    espn_athlete_id: str | None = Field(default=None, pattern=ESPN_ID)
    side: TeamSide | None = None
    description: str | None = Field(default=None, max_length=200)
    line: Decimal | None = Field(default=None, ge=-999, le=9999)
    american_odds: int | None = Field(default=None, ge=-100_000, le=100_000)
    tag_ids: list[int] = Field(default=[], max_length=20)


class SlipDraft(BaseModel):
    legs: list[LegDraft] = Field(min_length=1, max_length=20)
    sgp: bool = True  # "Same-game parlay": legs in one game are one, unless switched off
    american_odds: int | None = Field(default=None, ge=-100_000, le=100_000)  # as shown
    sportsbook_id: int | None = None
    is_placed: bool = False
    stake: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    potential_payout: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    boosted: bool = False
    notes: str | None = Field(default=None, max_length=500)
    source: EntrySource = EntrySource.QUICK_ADD


@dataclass
class Drafted:
    slip_type: SlipType
    american_odds: int | None   # the slip's odds: as entered, or worked out
    computed_odds: int | None   # a standard parlay's odds from its legs, for the hint
    slip: SlipIn | None = None  # None while anything is missing or wrong
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Leg:
    draft: LegDraft
    game: espn.Game | None
    player_name: str | None = None


def slip_type(games: list[espn.Game | None], sgp: bool) -> SlipType:
    if len(games) == 1:
        return SlipType.SINGLE
    ids = {g.espn_event_id if g else None for g in games}
    same_game = len(ids) == 1 and None not in ids
    return SlipType.SGP if same_game and sgp else SlipType.PARLAY


def computed_parlay_odds(odds: list[int | None]) -> int | None:
    if None in odds or any(-100 < o < 100 for o in odds):
        return None
    return american_odds(parlay_decimal(odds))


def messages(error: Exception) -> list[str]:
    if not isinstance(error, ValidationError):
        return [str(error)]
    out = []
    for e in error.errors():
        where = ""
        if len(e["loc"]) >= 2 and e["loc"][0] == "legs":
            where = f"Leg {e['loc'][1] + 1}: "
        elif e["loc"]:
            where = f"{str(e['loc'][-1]).replace('_', ' ').capitalize()}: "
        out.append(where + e["msg"].removeprefix("Value error, "))
    return out


def _money(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(Decimal("0.01"))


def _resolve(draft: SlipDraft, pickers: Pickers) -> tuple[list[_Leg], list[str]]:
    """Each leg's game, and its player's name, from the pickers' (cached) ESPN answers."""
    legs, errors = [], []
    for n, d in enumerate(draft.legs, start=1):
        game, problem = (None, None)
        if d.espn_event_id is not None:
            game, problem = pickers.game(d.sport, d.day, d.espn_event_id)
        leg = _Leg(d, game)
        legs.append(leg)
        if game is None:
            errors.append(f"Leg {n}: {problem}" if problem else f"Leg {n}: choose a game")
            continue
        if d.market_type in PLAYER_MARKETS and d.espn_athlete_id is not None:
            players, problem = pickers.players(d.sport, game)
            if d.espn_athlete_id in players:
                leg.player_name = players[d.espn_athlete_id][0]
            elif problem is None:  # the rosters loaded, and this player is on neither
                errors.append(f"Leg {n}: that player isn't on either team's roster")
    return legs, errors


def draft_slip(session: Session, draft: SlipDraft, pickers: Pickers, now: datetime, *,
               save_events: bool) -> Drafted:
    """Work out the slip from the form. With `save_events`, the games are stored as events
    (the caller then creates the slip and commits); without, only events already stored are
    looked up, for a preview that writes nothing."""
    legs, errors = _resolve(draft, pickers)
    games = [leg.game for leg in legs]
    kind = slip_type(games, draft.sgp)
    computed = computed_parlay_odds([leg.draft.american_odds for leg in legs])
    if kind is SlipType.SINGLE:
        odds = legs[0].draft.american_odds
    elif draft.american_odds is not None:
        odds = draft.american_odds
    else:
        odds = computed if kind is SlipType.PARLAY else None
    result = Drafted(kind, odds, computed if kind is SlipType.PARLAY else None)
    result.warnings = [f"Leg {n}: this game has already started"
                       for n, g in enumerate(games, start=1) if g and g.start_time <= now]
    if errors:
        result.errors = errors
        return result
    if odds is None:
        result.errors = ["Enter the slip's odds as shown by the sportsbook"]
        return result

    try:
        event_ids = _event_ids(session, [g for g in games if g is not None], save_events)
        placed = draft.is_placed
        data = SlipIn(
            is_placed=placed, slip_type=kind, sportsbook_id=draft.sportsbook_id,
            american_odds=odds, boosted=draft.boosted and kind is not SlipType.SINGLE,
            stake=_money(draft.stake) if placed else None,
            potential_payout=_money(draft.potential_payout) if placed else None,
            source=draft.source, notes=(draft.notes or "").strip() or None,
            legs=[_leg_in(leg, event_ids) for leg in legs])
    except (ValueError, ValidationError) as e:  # ServiceError is a ValueError
        result.errors = messages(e)
        return result
    result.slip = data
    result.warnings = services.slip_warnings(
        data, services.find_duplicates(session, data.legs)) + result.warnings
    return result


def _event_ids(session: Session, games: list[espn.Game], save: bool) -> dict[str, int]:
    if not save:
        ids = services.event_ids_for(session, [g.espn_event_id for g in games])
        # A game not stored yet gets a stand-in id of its own, below zero: nothing matches it.
        for n, g in enumerate(sorted({g.espn_event_id for g in games} - ids.keys()), start=1):
            ids[g] = -n
        return ids
    ids = {}
    for g in games:
        if g.espn_event_id not in ids:
            event = services.upsert_event(
                session, sport=g.sport, espn_event_id=g.espn_event_id,
                start_time=g.start_time, home_team=g.home.name, away_team=g.away.name,
                home_espn_team_id=g.home.espn_id, away_espn_team_id=g.away.espn_id,
                status=g.status)
            ids[g.espn_event_id] = event.id
    return ids


def _leg_in(leg: _Leg, event_ids: dict[str, int]) -> dict:
    """A `LegIn`'s fields, which SlipIn validates, so an error names its leg. Fields that don't
    belong to the market are dropped, as the form only showed the ones that do."""
    d, market = leg.draft, leg.draft.market_type
    assert leg.game is not None
    return dict(
        event_id=event_ids[leg.game.espn_event_id], market_type=market,
        side=d.side if market in TEAM_MARKETS else None,
        espn_athlete_id=d.espn_athlete_id if market in PLAYER_MARKETS else None,
        player_name=leg.player_name if market in PLAYER_MARKETS else None,
        description=((d.description or "").strip() or None) if market is MarketType.OTHER
        else None,
        line=None if market is MarketType.OTHER else d.line,
        american_odds=d.american_odds, tag_ids=d.tag_ids)
