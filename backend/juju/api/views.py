"""The read model: the cards for one player or one team, built from the archive and live stats.

Everything here reads Postgres only. No data provider is called while a person waits
(docs/GOALS.md section 6.1).
"""
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.core import card, plays
from juju.core.card import Bet, GameState, Outcome, PlayerStat
from juju.core.enums import EventStatus, PlayKind, Stat
from juju.core.live import Severity, age_seconds, freshness, game_status_text
from juju.core.markets import BY_KEY, NO, OVER, UNDER, YES, Market, Scope
from juju.core.models import Game, LiveStat, OddsSnapshot, Play, Player, Price
from juju.core.t45 import Listing, NoPrice, Offer, Quote, Selection, select as select_price

BOOK_NAMES = {
    "hardrockbet": "Hard Rock Bet", "draftkings": "DraftKings", "fanduel": "FanDuel",
    "betmgm": "BetMGM", "williamhill_us": "Caesars", "fanatics": "Fanatics",
    "betrivers": "BetRivers", "espnbet": "theScore Bet", "ballybet": "Bally Bet",
    "betparx": "betPARX",
}

NO_PRICE_TEXT = {
    NoPrice.NO_SNAPSHOT: "No price on file: Juju has no archived odds from before T-45 for "
                         "this game.",
    NoPrice.NOT_OFFERED: "No price on file: none of the books we follow offered this market "
                         "before T-45.",
    NoPrice.NOT_LISTED: "No price on file: the market was up at T-45, but not for this "
                        "player or line.",
    NoPrice.TOO_EARLY: "No price on file: the only prices we have are more than 3 hours "
                       "older than T-45.",
}

OUTCOME_TEXT = {
    Outcome.PREGAME: "Not started",
    Outcome.WAITING_FOR_FEED: "Waiting for the play to reach the feed",
    Outcome.LIVE: "Live",
    Outcome.LOCKED: "Cashed, if the play stands",
    Outcome.GONE: "Lost, if the play stands",
    Outcome.WON: "Won",
    Outcome.LOST: "Lost",
    Outcome.PUSH: "Push: stake back",
    Outcome.VOID: "Void: stake back",
    Outcome.NO_STAT_LINE: "No stat line: he never appeared in the box score",
    Outcome.UNTRACKED: "Juju can't follow this stat live",
    Outcome.UNAVAILABLE: "Live status unavailable right now",
}


@dataclass(frozen=True)
class Focus:
    """What the person asked about: a kind of play, a market, a threshold ("100+ yards")."""
    play: PlayKind | None = None
    market_key: str | None = None
    threshold: Decimal | None = None  # an Over line, e.g. 99.5 for "100+"
    expect: bool = False  # they described a play that should already count
    yards: int | None = None  # the play's length, if they gave one


NO_FOCUS = Focus()


@dataclass
class CardView:
    key: str
    label: str
    bet: str
    line: Decimal | None
    outcome: Outcome
    outcome_text: str
    current: Decimal | None
    needed: Decimal | None
    headline: str
    price: dict | None
    no_price: str | None
    returns: Decimal | None
    profit: Decimal | None
    fair_returns: Decimal | None
    fair_note: str | None
    others: list[dict]
    touched: bool
    named: bool
    alternate: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class GameView:
    id: int
    label: str
    home: dict
    away: dict
    status: EventStatus
    status_text: str
    commence_time: datetime
    freshness: Severity
    updated_seconds_ago: int | None


def game_view(game: Game, now: datetime) -> GameView:
    return GameView(
        id=game.id, label=game.label,
        home={"name": game.home_team, "abbr": game.home_abbr, "score": game.home_score,
              "espn_id": game.home_espn_id},
        away={"name": game.away_team, "abbr": game.away_abbr, "score": game.away_score,
              "espn_id": game.away_espn_id},
        status=game.status,
        status_text=game_status_text(game.status, game.period, game.clock_seconds,
                                     game.commence_time, now),
        commence_time=game.commence_time,
        freshness=freshness(game.status, game.last_polled_at, now),
        updated_seconds_ago=age_seconds(game.last_polled_at, now))


def _state(game: Game) -> GameState:
    return GameState(game.status, game.commence_time, game.home_score, game.away_score,
                     game.final_at, game.last_polled_at)


# --- Loading prices ----------------------------------------------------------------------------


@dataclass
class _Row:
    price: Price
    observed_at: datetime
    source: str
    sha: str


def _price_rows(session: Session, game_id: int, keys: Sequence[str],
                athlete: str | None = None) -> list[_Row]:
    q = (select(Price, OddsSnapshot.observed_at, OddsSnapshot.source,
                OddsSnapshot.payload_sha256)
         .join(OddsSnapshot, OddsSnapshot.id == Price.snapshot_id)
         .where(Price.game_id == game_id, Price.market_key.in_(keys)))
    if athlete is not None:
        q = q.where(Price.espn_athlete_id == athlete)
    return [_Row(p, at, str(src), sha) for p, at, src, sha in session.execute(q)]


def _listings(session: Session, game_id: int, keys: Sequence[str]) -> dict[str, list[Listing]]:
    rows = session.execute(
        select(Price.market_key, Price.book, Price.snapshot_id, OddsSnapshot.observed_at)
        .join(OddsSnapshot, OddsSnapshot.id == Price.snapshot_id)
        .where(Price.game_id == game_id, Price.market_key.in_(keys)).distinct())
    out: dict[str, list[Listing]] = defaultdict(list)
    for key, book, sid, at in rows:
        out[key].append(Listing(book, sid, at))
    return out


def _offers(rows: list[_Row], names: set[str], opposite: dict[str, str],
            point: Decimal | None, match_point: bool) -> list[Offer]:
    """Offers for outcomes named `names` (at `point` if `match_point`), each with the other
    side from the same snapshot, book, player and line."""
    index = {(r.price.snapshot_id, r.price.book, r.price.outcome_name, r.price.description,
              r.price.point): r.price.american for r in rows}
    out = []
    for r in rows:
        p = r.price
        if p.outcome_name not in names or (match_point and p.point != point):
            continue
        other = opposite.get(p.outcome_name)
        opp = index.get((p.snapshot_id, p.book, other, p.description, p.point)) if other else None
        out.append(Offer(p.book, p.american, p.point, r.observed_at, p.snapshot_id, r.source,
                         r.sha, p.market_last_update, opp))
    return out


# --- Building a card -------------------------------------------------------------------------


def _money_fields(sel: Selection, outcome: Outcome) -> dict:
    if sel.quote is None:
        return {"returns": None, "profit": None, "fair_returns": None, "fair_note": None,
                "headline": "No price on file"}
    m = card.money(sel.quote.offer.american, sel.quote.offer.opposite_american)
    back = card.settled_returns(outcome, m)
    if outcome in (Outcome.WON, Outcome.LOCKED):
        headline = f"$10 → ${m.returns}"
    elif back is not None:
        headline = f"$10 → ${back}"
    else:
        headline = f"$10 → ${m.returns} if it hits"
    note = None
    if m.fair_returns is None:
        note = "The book offered one side only, so there is no fair value to show."
    return {"returns": m.returns, "profit": m.profit, "fair_returns": m.fair_returns,
            "fair_note": note, "headline": headline}


def _price_dict(q: Quote) -> dict:
    o = q.offer
    return {"american": o.american, "book": o.book, "book_name": BOOK_NAMES.get(o.book, o.book),
            "point": o.point, "observed_at": o.observed_at, "timing": q.timing.value,
            "minutes_before_kickoff": q.minutes_before_kickoff, "source": o.source,
            "market_last_update": o.market_last_update, "provenance": o.payload_sha256[:12]}


def _other_dicts(sel: Selection) -> list[dict]:
    return [{"book_name": BOOK_NAMES.get(q.offer.book, q.offer.book),
             "american": q.offer.american, "point": q.offer.point, "timing": q.timing.value}
            for q in sel.others]


def _bet_text(market: Market, line: Decimal | None, side: str | None) -> str:
    if market.first_td:
        return "Yes: scores the game's first touchdown"
    if market.yes_only:
        return f"Yes: {market.label.lower()}"
    if market.scope is Scope.MONEYLINE:
        return f"{side} to win"
    if market.scope is Scope.SPREAD:
        return f"{side} {'+' if line is not None and line > 0 else ''}{line}"
    if market.scope is Scope.TEAM:
        return f"{side} over {line} points"
    if market.scope is Scope.TOTAL:
        return f"Over {line} points"
    if market.alternate and line is not None:
        return f"{line + Decimal('0.5'):g}+ {market.label.lower()}"
    return f"Over {line} {market.label.lower()}"


# Whether the feed already shows the play someone described: (stat, minimum) per kind of play.
# With yards, the longest-play stat must reach them.
_CLAIMS: dict[PlayKind, tuple[Stat, Stat | None]] = {
    PlayKind.TOUCHDOWN: (Stat.TOUCHDOWNS, None),
    PlayKind.PASS_TD: (Stat.PASS_TDS, None),
    PlayKind.RUN: (Stat.RUSH_ATTEMPTS, Stat.LONGEST_RUSH),
    PlayKind.CATCH: (Stat.RECEPTIONS, Stat.LONGEST_RECEPTION),
    PlayKind.PASS: (Stat.PASS_COMPLETIONS, None),
    PlayKind.FIELD_GOAL: (Stat.FIELD_GOALS, None),
    PlayKind.INTERCEPTION: (Stat.INTERCEPTIONS_THROWN, None),
    PlayKind.DEF_INTERCEPTION: (Stat.DEF_INTERCEPTIONS, None),
    PlayKind.SACK: (Stat.SACKS, None),
}


def claim_in_feed(focus: Focus, stats: dict[Stat, Decimal]) -> bool:
    """Does the box score already reflect the play they described? Unknown claims count as
    reflected: waiting is only shown when we can tell the feed is behind."""
    if not focus.expect or focus.play not in _CLAIMS:
        return True
    count_stat, longest_stat = _CLAIMS[focus.play]
    if focus.yards is not None and longest_stat is not None:
        return stats.get(longest_stat, Decimal(0)) >= focus.yards
    return stats.get(count_stat, Decimal(0)) >= 1


def _build(market: Market, sel: Selection, bet: Bet, game: Game, stat: PlayerStat | None,
           now: datetime, focus: Focus, side: str | None = None,
           claim_seen: bool = True) -> CardView:
    decided = card.decide(bet, _state(game), stat, now)
    outcome = decided.outcome
    touched = plays.touches(market, focus.play)
    named = focus.market_key is not None and (
        focus.market_key == market.key or focus.market_key == market.key.replace(
            "_alternate", ""))
    if (not claim_seen and touched and outcome is Outcome.LIVE
            and game.status is not EventStatus.FINAL):
        outcome = Outcome.WAITING_FOR_FEED
    notes = []
    if game.corrected_at is not None and outcome in card.SETTLED:
        notes.append("An official stat for this game changed after it settled.")
    if outcome in card.SETTLED and market.scope is Scope.PLAYER:
        notes.append("Awaiting verification against the official play-by-play.")
    return CardView(
        key=market.key, label=market.label, bet=_bet_text(market, bet.line, side),
        line=bet.line, outcome=outcome, outcome_text=OUTCOME_TEXT[outcome],
        current=decided.current, needed=decided.needed,
        price=_price_dict(sel.quote) if sel.quote else None,
        no_price=NO_PRICE_TEXT[sel.reason] if sel.reason else None,
        others=_other_dicts(sel), touched=touched, named=named, alternate=market.alternate,
        notes=notes, **_money_fields(sel, outcome))


# --- Player -----------------------------------------------------------------------------------


def first_td_scorer(session: Session, game_id: int) -> str | None:
    """The athlete who scored the game's first touchdown; `card.UNKNOWN_SCORER` if a touchdown
    happened but wasn't matched to a player; None if there hasn't been one."""
    first = session.scalars(select(Play).where(
        Play.game_id == game_id, Play.kind == PlayKind.TOUCHDOWN.value, Play.scoring.is_(True))
        .order_by(Play.period, Play.sequence).limit(1)).first()
    if first is None:
        return None
    return first.espn_athlete_id or card.UNKNOWN_SCORER


def player_stats(session: Session, game_id: int, athlete: str) -> dict[Stat, Decimal]:
    return dict(session.execute(select(LiveStat.stat, LiveStat.value).where(
        LiveStat.game_id == game_id, LiveStat.espn_athlete_id == athlete)).all())


@dataclass
class PlayerView:
    game: GameView
    player: dict
    cards: list[CardView]
    waiting_for_feed: bool


_YES_NAMES = {YES, OVER}  # a yes-only market may come back as "Yes" or as "Over 0.5"
_OPPOSITE = {OVER: UNDER, YES: NO}


def player_view(session: Session, game: Game, athlete: str, now: datetime,
                chain: Sequence[str], focus: Focus = NO_FOCUS) -> PlayerView | None:
    player = session.get(Player, athlete)
    if player is None:
        return None
    keys = sorted({k for (k,) in session.execute(select(Price.market_key).where(
        Price.game_id == game.id, Price.espn_athlete_id == athlete).distinct())})
    rows_by_key: dict[str, list[_Row]] = defaultdict(list)
    for r in _price_rows(session, game.id, keys, athlete):
        rows_by_key[r.price.market_key].append(r)
    listings = _listings(session, game.id, keys)
    stats = player_stats(session, game.id, athlete)
    scorer = first_td_scorer(session, game.id)
    claim_seen = claim_in_feed(focus, stats)
    views: list[CardView] = []
    for key in keys:
        market = BY_KEY.get(key)
        if market is None or market.scope is not Scope.PLAYER:
            continue
        rows = rows_by_key[key]
        if market.alternate:
            if focus.threshold is None:
                continue  # ladders only when someone names a threshold
            offers = _offers(rows, {OVER}, _OPPOSITE, focus.threshold, True)
        elif market.yes_only:
            offers = _offers(rows, _YES_NAMES, _OPPOSITE, None, False)
        else:
            offers = _offers(rows, {OVER}, _OPPOSITE, None, False)
        sel = select_price(offers, listings.get(key, []), game.commence_time, chain)
        line = None
        if sel.quote is not None:
            line = sel.quote.offer.point
        if market.yes_only and not market.first_td:
            line = Decimal("0.5")
        if market.alternate:
            line = focus.threshold
        value = stats.get(market.stat) if market.stat else None
        stat = PlayerStat(value, scorer, athlete)
        views.append(_build(market, sel, Bet(market, line), game, stat, now, focus,
                            claim_seen=claim_seen))
    ranked = plays.rank([plays.Ranked(BY_KEY[v.key], v.outcome, v.touched, v.named)
                         for v in views])
    order = {(r.market.key): i for i, r in enumerate(ranked)}
    views.sort(key=lambda v: order[v.key])
    team_abbr = (game.home_abbr if player.team_espn_id == game.home_espn_id
                 else game.away_abbr if player.team_espn_id == game.away_espn_id else None)
    return PlayerView(
        game=game_view(game, now),
        player={"id": athlete, "name": player.name, "team_abbr": team_abbr,
                "position": player.position},
        cards=views,
        waiting_for_feed=any(v.outcome is Outcome.WAITING_FOR_FEED for v in views))


# --- Team -------------------------------------------------------------------------------------

TEAM_KEYS = ("h2h", "spreads", "team_totals", "totals")


@dataclass
class TeamView:
    game: GameView
    team: dict
    cards: list[CardView]


def team_view(session: Session, game: Game, team_espn_id: str, now: datetime,
              chain: Sequence[str], focus: Focus = NO_FOCUS) -> TeamView | None:
    if team_espn_id not in (game.home_espn_id, game.away_espn_id):
        return None
    is_home = team_espn_id == game.home_espn_id
    name = game.home_team if is_home else game.away_team
    rows = _price_rows(session, game.id, TEAM_KEYS)
    listings = _listings(session, game.id, TEAM_KEYS)
    by_key: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        by_key[r.price.market_key].append(r)
    other_team = game.away_team if is_home else game.home_team
    views = []
    for key in TEAM_KEYS:
        market = BY_KEY[key]
        rs = by_key[key]
        if market.scope in (Scope.MONEYLINE, Scope.SPREAD):
            offers = _offers(rs, {name}, {name: other_team}, None, False)
        elif market.scope is Scope.TEAM:
            offers = _offers([r for r in rs if r.price.description == name], {OVER},
                             _OPPOSITE, None, False)
        else:
            offers = _offers(rs, {OVER}, _OPPOSITE, None, False)
        sel = select_price(offers, listings.get(key, []), game.commence_time, chain)
        line = sel.quote.offer.point if sel.quote else None
        bet = Bet(market, line, side_is_home=is_home)
        side = (game.home_abbr if is_home else game.away_abbr) or name
        views.append(_build(market, sel, bet, game, None, now, focus, side))
    return TeamView(game_view(game, now),
                    {"espn_id": team_espn_id, "name": name,
                     "abbr": game.home_abbr if is_home else game.away_abbr}, views)
