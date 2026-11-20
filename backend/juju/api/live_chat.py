"""Live odds on demand: the chat's endpoints (docs/plans/live-chat.md, GOALS decision 14).

This is the one router whose requests reach The Odds API, through `ingest/live_odds.py`: a cache
of at most 60 seconds, a lock per cache key, and the credits left as The Odds API reports them.
It reads the archive for nothing: no `Price`, no snapshot. The database is used only to say
where a player stands in the game (`LiveStat`) and, for "yards" with no kind, his position.

Licensing (docs/licensing.md): an answer is one player's or one team's lines in one game, or
the 2 to 6 legs someone picked. A request with no player or team is refused. The schedule and
the list of markets carry no prices.
"""
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from juju.api.common import SessionDep, respond
from juju.api.lookup import NICKNAMES
from juju.api.views import BOOK_NAMES
from juju.config import Settings, get_settings
from juju.core import budget, livechat
from juju.core.enums import PlayKind
from juju.core.livechat import MAX_LEGS, Answer, Posting, Price
from juju.core.markets import BY_KEY, CAPTURE_PRIORITY, Market, Scope
from juju.core.models import Game, LiveStat, Player, SourceHealth
from juju.db import session_factory
from juju.ingest.http import RateLimiter
from juju.ingest.live_odds import (
    Fetched, LiveError, LiveOdds, market_offered, postings_for, subjects,
)
from juju.ingest.odds_api import SOURCE, ApiEvent, OddsApiClient, key_fingerprint
from juju.ingest.parse import LiveRead, read_live
from juju.ingest.resolve import (
    RosterEntry, is_confident, mentions, normalize_name, rank_players, same_team, team_aliases,
)
from juju.ingest.router import Breakers

log = logging.getLogger("juju.live_chat")

router = APIRouter()

DISCLAIMER = ("Live prices, as of the time shown. Juju is not a sportsbook and does not take "
              "bets.")
EVENT_ID = re.compile(r"^[A-Za-z0-9]{6,64}$")
# "Fannin 4 catches and Metcalf 60 yards": the same split the archive's parlays use
# (`api/lookup.py`); "100+" has no space before its "+", so it never splits.
_PARTS = re.compile(r"\s*(?:,|;|&|\band\b|\s\+\s)\s*", re.IGNORECASE)
_PLAY_MARKETS = {PlayKind.TOUCHDOWN: "player_anytime_td", PlayKind.PASS_TD: "player_pass_tds"}
_ALTERNATES = {"spreads": "alternate_spreads", "totals": "alternate_totals",
               "team_totals": "alternate_team_totals"}
_YARDS_BY_POSITION = {"WR": "player_reception_yds", "TE": "player_reception_yds",
                      "QB": "player_pass_yds"}
EXAMPLES = "Try \"Fannin 4 catches\", \"Metcalf over 45.5 receiving yards\" or \"Browns -3.5\"."


# --- The live source, built once -------------------------------------------------------------

_live: LiveOdds | None = None
_live_lock = threading.Lock()


def _stored_quota(key: str, reset_day: int) -> int | None:
    """What the worker last stored for this key, if the credits haven't reset since."""
    try:
        with session_factory()() as session:
            row = session.get(SourceHealth, SOURCE)
            if row is None or row.quota_key != key_fingerprint(key):
                return None
            return budget.current_quota(row.quota_remaining, row.quota_at,
                                        datetime.now(tz=UTC), reset_day)
    except Exception:  # noqa: BLE001 - the count is a convenience: the first reply says
        return None


def _quota_writer(key: str):
    """After a live call, store the credits left in the shared row, and nothing else of it:
    the worker's breaker state is the worker's."""
    fingerprint = key_fingerprint(key)

    def write(remaining: int, at: datetime) -> None:
        with session_factory()() as session:
            session.execute(update(SourceHealth).where(SourceHealth.source == SOURCE).values(
                quota_remaining=remaining, quota_key=fingerprint, quota_at=at))
            session.commit()

    return write


def _build(settings: Settings) -> LiveOdds:
    if settings.live_odds_fixture:  # development and e2e only (dev_live.py): no key, no credits
        from juju.dev_live import FixtureLive
        source = FixtureLive()
        return LiveOdds(source, settings.books, odds_ttl=settings.live_cache_seconds,
                        markets_ttl=settings.live_markets_cache_seconds,
                        initial_remaining=source.quota_remaining)
    assert settings.odds_api_key is not None
    key = settings.odds_api_key.get_secret_value()
    # Its own in-memory breaker: a bad hour for live questions never opens the worker's.
    client = OddsApiClient(key, Breakers(engine=None),
                           RateLimiter(per_host_interval=0.0, per_minute=120),
                           reset_day=settings.odds_api_reset_day)
    return LiveOdds(client, settings.books, odds_ttl=settings.live_cache_seconds,
                    markets_ttl=settings.live_markets_cache_seconds,
                    initial_remaining=_stored_quota(key, settings.odds_api_reset_day),
                    on_quota=_quota_writer(key))


def live_odds(settings: Annotated[Settings, Depends(get_settings)]) -> LiveOdds:
    global _live
    if settings.odds_api_key is None and not settings.live_odds_fixture:
        raise HTTPException(503, "Live odds aren't set up on this server: it has no Odds API "
                                 "key.")
    with _live_lock:
        if _live is None:
            _live = _build(settings)
        return _live


LiveDep = Annotated[LiveOdds, Depends(live_odds)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- Shared pieces ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def credits_block(live: LiveOdds, settings: Settings) -> dict:
    remaining = live.remaining
    state = livechat.credit_state(remaining, settings.live_low_credits)
    note = None
    if state == "low":
        note = (f"Only {remaining:,} odds credits are left this month. Live questions come "
                "first, so the archive may go without prices for some games.")
    elif state == "out":
        note = "Juju's odds credits are used up for the month, so there are no live prices."
    return {"remaining": remaining, "state": state, "low_at": settings.live_low_credits,
            "spent": live.spent, "note": note}


def _event(live: LiveOdds, game_id: str) -> ApiEvent:
    """The game, from the schedule. An id that isn't on it is refused before any credit is
    spent, so a made-up id can't cost anything."""
    if not EVENT_ID.match(game_id):
        raise HTTPException(422, "Not a game id.")
    try:
        schedule = live.games().value
    except LiveError as e:
        raise HTTPException(503, e.message) from e
    event = next((g for g in schedule if g.id == game_id), None)
    if event is None:
        raise HTTPException(404, "That game isn't on the live schedule.")
    return event


def _price_view(p: Price | None) -> dict | None:
    if p is None:
        return None
    return {"book": p.book, "book_name": BOOK_NAMES.get(p.book, p.book), "american": p.american,
            "returns": p.returns, "last_update": p.last_update}


def _alternate_of(key: str) -> str | None:
    alt = _ALTERNATES.get(key) or f"{key}_alternate"
    return alt if alt in BY_KEY and alt != key and not BY_KEY[key].alternate else None


def _market_for(read: LiveRead) -> str | None:
    return read.parsed.market_key or (_PLAY_MARKETS.get(read.parsed.play)
                                      if read.parsed.play else None)


def _team_in(event: ApiEvent, who: str) -> str | None:
    """Which of the game's teams the text names (its names, nicknames and abbreviations)."""
    text = normalize_name(who)
    hits = []
    for team in (event.home_team, event.away_team):
        aliases = set(team_aliases(team, None)) | set(NICKNAMES.get(normalize_name(team), ()))
        if text and mentions(text, aliases):
            hits.append(team)
    return hits[0] if len(hits) == 1 else None


def _subject_in(who: str, names: list[str]) -> tuple[str | None, list[str]]:
    """The one name in a market that `who` means, or the nearest ones to ask about."""
    ranked = rank_players(who, [RosterEntry(n, n) for n in names])
    if is_confident(ranked):
        return ranked[0].entry.name, []
    return None, [c.entry.name for c in ranked[:5]]


# --- What the database knows (never a price) -------------------------------------------------


def _roster(session: Session, event_id: str) -> tuple[Game | None, list[Player]]:
    game = session.scalar(select(Game).where(Game.odds_event_id == event_id))
    if game is None:
        return None, []
    teams = [t for t in (game.home_espn_id, game.away_espn_id) if t]
    players = list(session.scalars(select(Player).where(Player.team_espn_id.in_(teams)))) \
        if teams else []
    return game, players


def _player_in_db(who: str, players: list[Player]) -> Player | None:
    ranked = rank_players(who, [RosterEntry(p.espn_athlete_id, p.name, p.team_espn_id)
                                for p in players])
    if not is_confident(ranked):
        return None
    wanted = ranked[0].entry.espn_athlete_id
    return next((p for p in players if p.espn_athlete_id == wanted), None)


def _yards_market(session: Session, event_id: str, who: str) -> str | None:
    """"Yards" with no kind: a receiver's are receiving, a quarterback's passing. A running
    back's could be either, so he is asked."""
    _, players = _roster(session, event_id)
    player = _player_in_db(who, players)
    return _YARDS_BY_POSITION.get(player.position or "") if player else None


def _stands(session: Session, event_id: str, name: str, market: Market) -> str | None:
    """"Fannin has 3 receptions so far." From the box score Juju already follows; None when
    the game isn't followed, or he has no stat line yet (never a zero made up)."""
    if market.scope is not Scope.PLAYER or market.stat is None or market.alternate:
        return None
    game, players = _roster(session, event_id)
    player = _player_in_db(name, players) if game else None
    if game is None or player is None:
        return None
    rows = {r.stat: r.value for r in session.scalars(select(LiveStat).where(
        LiveStat.game_id == game.id, LiveStat.espn_athlete_id == player.espn_athlete_id))}
    if not rows:
        return None
    value = rows.get(market.stat, Decimal(0))
    return f"{name} has {format(value.normalize(), 'f')} {market.label.lower()} so far."


# --- GET /api/live/games ---------------------------------------------------------------------


@router.get("/api/live/games")
def games(live: LiveDep, settings: SettingsDep):
    """The live schedule (free: The Odds API charges nothing for it). No prices."""
    try:
        fetched = live.games()
    except LiveError as e:
        raise HTTPException(503, e.message) from e
    now = _utcnow()
    rows = sorted(fetched.value, key=lambda g: g.commence_time)
    return respond({
        "games": [{"id": g.id, "label": f"{g.away_team} @ {g.home_team}", "home": g.home_team,
                   "away": g.away_team, "commence_time": g.commence_time,
                   "in_play": g.commence_time <= now} for g in rows],
        "as_of": fetched.fetched_at, "credits": credits_block(live, settings)})


# --- GET /api/live/markets -------------------------------------------------------------------


@router.get("/api/live/markets")
def markets(live: LiveDep, settings: SettingsDep, game_id: str):
    """Which markets the books offer for this game: names only, no prices. One call to The Odds
    API (1 credit), cached for five minutes; the page asks it when someone opens a game."""
    _event(live, game_id)
    try:
        fetched = live.markets(game_id)
    except LiveError as e:
        return respond(_failure(live, settings, e))
    order = {k: i for i, k in enumerate(CAPTURE_PRIORITY)}
    offered = sorted((k for k in fetched.value if k in BY_KEY), key=lambda k: order[k])
    return respond({
        "markets": [{"key": k, "label": BY_KEY[k].label, "scope": BY_KEY[k].scope,
                     "alternate": BY_KEY[k].alternate} for k in offered],
        "as_of": fetched.fetched_at, "cached": fetched.cached, "cost": fetched.cost,
        "credits": credits_block(live, settings)})


def _failure(live: LiveOdds, settings: Settings, e: LiveError, **more) -> dict:
    return {"kind": e.kind, "message": e.message, "credits": credits_block(live, settings),
            "disclaimer": DISCLAIMER, **more}


# --- GET /api/live-markets (the primitive) ---------------------------------------------------


def _line_views(postings: list[Posting], chain: list[str]) -> list[dict]:
    sides = list(dict.fromkeys(p.name for p in postings))
    by_point: dict[Decimal | None, dict[str, list[dict]]] = {}
    for side in sides:
        for pick in livechat.answer_for(postings, side, None, chain).picks:
            by_point.setdefault(pick.point, {})[side] = [
                v for v in (_price_view(p) for p in pick.prices) if v is not None]
    ordered = sorted(by_point, key=lambda p: (p is not None, p))
    return [{"point": p, "sides": by_point[p]} for p in ordered]


@router.get("/api/live-markets")
def live_markets(live: LiveDep, settings: SettingsDep, game_id: str, market: str,
                 player: Annotated[str, Query(max_length=60)] = ""):
    """One player's (or team's) live lines in one market of one game, from every book in the
    chain: over and under, each line, each book's price. `player` is required: this is never a
    board of every player (docs/licensing.md)."""
    who = player.strip()
    if not who:
        raise HTTPException(400, "Name a player or team. Juju shows one at a time, never a "
                                 "whole market.")
    if market not in BY_KEY:
        raise HTTPException(422, "Not a market Juju knows.")
    spec = BY_KEY[market]
    event = _event(live, game_id)
    try:
        fetched = live.odds(game_id, market)
    except LiveError as e:
        raise HTTPException(404 if e.kind == "not_offered" else 503, e.message) from e
    label = spec.label
    if not market_offered(fetched.value, market):
        raise HTTPException(404, f"Market not available: no book is posting {label} for this "
                                 "game right now.")
    matches, subject = _matcher(spec, who, event, fetched.value, market)
    if matches is None:
        names = subjects(fetched.value, market) if spec.scope in (Scope.PLAYER, Scope.TEAM) \
            else []
        near = _subject_in(who, names)[1] if names else []
        raise HTTPException(404, f"Market not available: no book is posting {label} for "
                                 f"\"{who}\" in this game right now."
                            + (f" Did you mean {', '.join(near)}?" if near else ""))
    postings = postings_for(fetched.value, {market}, matches)
    return respond({
        "game_id": game_id, "market": market, "label": label, "subject": subject,
        "lines": _line_views(postings, settings.books),
        "as_of": fetched.fetched_at, "cached": fetched.cached, "cost": fetched.cost,
        "credits": credits_block(live, settings), "disclaimer": DISCLAIMER})


def _matcher(spec: Market, who: str, event: ApiEvent, reply, market: str):
    """A function that picks one subject's outcomes out of a reply, and his name; (None, None)
    when the reply doesn't name him."""
    if spec.scope is Scope.TOTAL:
        return (lambda s: True), "Game total"
    if spec.scope in (Scope.TEAM, Scope.SPREAD, Scope.MONEYLINE):
        team = _team_in(event, who)
        if team is None:
            return None, None
        return (lambda s, t=team: s is not None and same_team(s, t)), team
    name, _ = _subject_in(who, subjects(reply, market))
    if name is None:
        return None, None
    return (lambda s, n=name: s == n), name


# --- GET /api/live/plan (free) ---------------------------------------------------------------


@router.get("/api/live/plan")
def plan(live: LiveDep, settings: SettingsDep, game_id: str,
         q: Annotated[str, Query(max_length=200)] = ""):
    """What a question would cost, before it is asked. No call to The Odds API: it only reads the
    words and looks in the cache."""
    _event(live, game_id)
    parts = [x for x in _PARTS.split(q) if x.strip()][:MAX_LEGS]
    keys: list[str] = []
    alternates: dict[str, bool] = {}
    unsure = False
    for part in parts:
        read = read_live(part)
        key = _market_for(read)
        if key is None:
            unsure = True  # "yards" or a missing stat: one market, which one is decided later
            continue
        keys.append(key)
        asked = read.line is not None or read.handicap is not None
        alternates[key] = asked and _alternate_of(key) is not None
    if unsure and not keys:
        keys = ["?"]
    cached = {k for k in keys if k != "?" and live.cached(game_id, k)}
    cost = livechat.cost_of(keys, cached, alternates)
    return respond({"cost": {"low": cost.low, "high": cost.high, "cached": cost.cached,
                             "text": cost.text},
                    "credits": credits_block(live, settings)})


# --- POST /api/live/ask ----------------------------------------------------------------------


class AskIn(BaseModel):
    game_id: str = Field(min_length=6, max_length=64)
    text: str = Field(min_length=1, max_length=200)


@dataclass
class Leg:
    read: LiveRead
    market: Market
    team: str | None = None       # a team market's team (or a spread's side)
    subject: str | None = None    # the player's name, as the books print it
    side: str = "Over"
    answer: Answer | None = None
    stands: str | None = None
    missing: str | None = None    # why this leg has no price


def _reply(live: LiveOdds, settings: Settings, kind: str, message: str, **more) -> dict:
    return {"kind": kind, "message": message, "sentences": [message], "legs": [], "parlay": None,
            "choices": [], "as_of": None, "cached": True, "cost": 0,
            "credits": credits_block(live, settings), "disclaimer": DISCLAIMER, **more}


def _asked_line(leg: Leg) -> Decimal | None:
    m = leg.market
    if m.yes_only or m.scope is Scope.MONEYLINE:
        return None
    return leg.read.handicap if m.scope is Scope.SPREAD else leg.read.line


def _side(leg: Leg) -> str | None:
    m = leg.market
    if m.scope in (Scope.SPREAD, Scope.MONEYLINE):
        return None
    return livechat.YES if m.yes_only else leg.read.side


def _plan_leg(session: Session, event: ApiEvent, read: LiveRead) -> Leg | str:
    """Decide what bet one part names, without spending a credit; or say what's missing."""
    key = _market_for(read)
    if key is None and read.yards_of_any_kind:
        key = _yards_market(session, event.id, read.who)
        if key is None:
            return ("Receiving, rushing or passing yards? Say which, for example "
                    f"\"{read.who or 'Fannin'} over 45.5 receiving yards\".")
    if key is None:
        return f"I couldn't tell which bet that is. {EXAMPLES}"
    market = BY_KEY[key]
    leg = Leg(read, market)
    if market.scope in (Scope.TEAM, Scope.SPREAD, Scope.MONEYLINE):
        leg.team = _team_in(event, read.who)
        if leg.team is None:
            return (f"Which team? This game is {event.away_team} at {event.home_team}. "
                    f"{EXAMPLES}")
    elif market.scope is Scope.PLAYER and not read.who:
        return f"Which player? {EXAMPLES}"
    leg.side = _side(leg) or "Over"
    return leg


def _answer_leg(leg: Leg, event: ApiEvent, replies: dict[str, Fetched], chain: list[str],
                live: LiveOdds) -> None:
    """Fill in one leg from the replies already fetched; fetch its alternate market (one more
    credit) only if the exact line asked for isn't in the main one."""
    spec = leg.market
    main = replies[spec.key].value
    if not market_offered(main, spec.key):
        leg.missing = (f"Market not available: no book is posting {spec.label} for this "
                       "game right now.")
        return
    matches, name = _matcher(spec, leg.read.who, event, main, spec.key)
    if matches is None:
        near = _subject_in(leg.read.who, subjects(main, spec.key))[1] \
            if spec.scope in (Scope.PLAYER, Scope.TEAM) else []
        who = leg.read.who or "that"
        leg.missing = (f"Market not available: no book is posting {spec.label} for \"{who}\" "
                       "in this game right now."
                       + (f" Did you mean {', '.join(near)}?" if near else ""))
        return
    leg.subject = None if spec.scope is Scope.TOTAL else name  # a total is nobody's
    asked, side = _asked_line(leg), _side(leg)
    postings = postings_for(main, {spec.key}, matches)
    answer = livechat.answer_for(postings, side, asked, chain)
    alt = _alternate_of(spec.key)
    if not answer.exact and asked is not None and alt is not None:
        fetched = replies.get(alt) or live.odds(event.id, alt)
        replies[alt] = fetched
        postings += postings_for(fetched.value, {alt}, matches)
        answer = livechat.answer_for(postings, side, asked, chain)
    leg.answer = answer


def _bet(leg: Leg, pick_point: Decimal | None) -> str:
    m = leg.market
    return livechat.bet_text(
        m.label, leg.subject or leg.team, _side(leg), pick_point, yes_only=m.yes_only,
        spread=m.scope is Scope.SPREAD, moneyline=m.scope is Scope.MONEYLINE)


def _leg_sentences(leg: Leg) -> list[str]:
    assert leg.answer is not None
    a, names = leg.answer, BOOK_NAMES
    m = leg.market
    if not a.picks:
        return [f"Market not available: no book is posting {m.label} "
                f"{'for ' + (leg.subject or '') + ' ' if leg.subject else ''}on that side "
                "right now.".replace("  ", " ")]
    out = []
    if not a.exact and a.asked is not None:
        out.append(livechat.missing_line_sentence(a.asked, a, m.label, _side(leg), names))
        return out
    pick = a.picks[0]
    out.append(livechat.lead_sentence(_bet(leg, pick.point), pick, names))
    if len(a.picks) > 1:  # no line was asked for and several are posted
        extra = "; ".join(f"{_bet(leg, p.point)} {livechat.american_text(p.book.american)}"
                          for p in a.picks[1:])
        out.append(f"Also posted: {extra}.")
    if (o := livechat.others_sentence(pick, names)):
        out.append(o)
    if leg.stands:
        out.append(leg.stands)
    return out


def _leg_view(leg: Leg) -> dict:
    a = leg.answer
    return {
        "market": leg.market.key, "label": leg.market.label,
        "subject": leg.subject or leg.team, "side": _side(leg),
        "asked": None if a is None else a.asked, "exact": bool(a and a.exact),
        "available": bool(a and a.picks), "missing": leg.missing, "stands": leg.stands,
        "picks": [] if a is None else [
            {"point": p.point, "bet": _bet(leg, p.point), "book": _price_view(p.book),
             "others": [_price_view(x) for x in p.others],
             "opposite": _price_view(p.opposite)} for p in a.picks]}


@router.post("/api/live/ask")
def ask(body: AskIn, session: SessionDep, live: LiveDep, settings: SettingsDep):
    """Free text about this game in, one plain-language answer out. Fetches live odds only for
    what was asked (cached for a minute), and never anything else."""
    event = _event(live, body.game_id)
    try:
        return respond(_ask(body, event, session, live, settings))
    except LiveError as e:
        return respond(_failure(live, settings, e))


def _ask(body: AskIn, event: ApiEvent, session: Session, live: LiveOdds,
         settings: Settings) -> dict:
    parts = [x for x in _PARTS.split(body.text) if x.strip()]
    if not parts:
        return _reply(live, settings, "ask", f"Ask about a player or team. {EXAMPLES}")
    if len(parts) > MAX_LEGS:
        return _reply(live, settings, "ask", f"A parlay has 2 to {MAX_LEGS} legs.")
    legs: list[Leg] = []
    for part in parts:
        planned = _plan_leg(session, event, read_live(part))
        if isinstance(planned, str):
            return _reply(live, settings, "ask", planned)
        legs.append(planned)

    # Fetch each distinct market once (the cache makes a repeat free); then answer each leg.
    replies: dict[str, Fetched] = {}
    for key in dict.fromkeys(leg.market.key for leg in legs):
        replies[key] = live.odds(event.id, key)
    for leg in legs:
        _answer_leg(leg, event, replies, settings.books, live)
        if leg.subject and leg.answer and leg.answer.picks:
            leg.stands = _stands(session, event.id, leg.subject, leg.market)
    used = list(replies.values())  # the alternates a leg needed were added as it was answered
    meta = {"as_of": min(f.fetched_at for f in used), "cached": all(f.cached for f in used),
            "cost": sum(f.cost for f in used)}

    unavailable = [leg for leg in legs if leg.answer is None]
    if unavailable:
        message = " ".join(leg.missing or "" for leg in unavailable).strip()
        return {**_reply(live, settings, "unavailable", message), **meta,
                "legs": [_leg_view(leg) for leg in legs if leg.answer is not None]
                + [{"market": leg.market.key, "label": leg.market.label, "available": False,
                    "missing": leg.missing} for leg in unavailable]}

    sentences: list[str] = []
    for leg in legs:
        sentences += _leg_sentences(leg)
    kind, parlay = "answer", None
    if len(legs) > 1:
        kind = "parlay"
        picks = [leg.answer.picks[0] for leg in legs
                 if leg.answer and leg.answer.exact and leg.answer.picks]
        priced = livechat.price_parlay(picks, settings.books) if len(picks) == len(legs) else None
        if priced is None:
            sentences.append("I can't price the parlay: every leg needs a posted line.")
        else:
            sentences.append(livechat.parlay_sentence(len(legs), priced, BOOK_NAMES))
            sentences.extend(priced.notes)
            parlay = {"book": priced.book,
                      "book_name": BOOK_NAMES.get(priced.book, priced.book) if priced.book
                      else None, "returns": priced.returns, "notes": list(priced.notes),
                      "legs": [_price_view(p) for p in priced.legs]}
    return {"kind": kind, "message": " ".join(sentences), "sentences": sentences,
            "legs": [_leg_view(leg) for leg in legs], "parlay": parlay, "choices": [],
            **meta, "credits": credits_block(live, settings), "disclaimer": DISCLAIMER}

