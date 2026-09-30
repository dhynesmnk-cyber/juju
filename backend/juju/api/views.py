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

from juju.core import card, parlay, plays
from juju.core.card import Bet, GameState, Outcome, PlayerStat
from juju.core.enums import DataSource, EventStatus, PlayKind, Stat
from juju.core.live import Severity, age_seconds, freshness, game_status_text
from juju.core.markets import BY_KEY, NO, OVER, UNDER, YES, Market, Scope
from juju.core.models import (
    DecidingPlay, Game, LiveStat, OddsSnapshot, Play, Player, Price, StatCorrection,
)
from juju.core.t45 import Listing, NoPrice, Offer, Quote, Selection, select as select_price
from juju.ingest.nflverse import CHECKED_STATS, MAX_STATS

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

NO_LINE_TEXT = "No line to follow: there is no price on file"

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
    verified: bool = False  # the settled number matches the official stats
    # The play that took it past the line: {text, period, clock, exact}. `exact` False is the
    # live feed's "on or around" (text may be None: only the game clock is known).
    decided_by: dict | None = None
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
        return f"{_fmt(line + Decimal('0.5'))}+ {market.label.lower()}"  # 99.5 -> "100+"
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


# --- The next-day check ----------------------------------------------------------------------

# Outcomes that rest on a final number the official stats can confirm (a void rests on none).
_CHECKED_OUTCOMES = card.SETTLED - {Outcome.VOID}


@dataclass(frozen=True)
class Check:
    """What the next-day check against nflverse says about the number a card settled on."""
    checkable: bool                # nflverse has this stat at all
    verified: bool = False         # it gave the same number (or the one now shown)
    corrections: tuple[StatCorrection, ...] = ()


UNCHECKED = Check(checkable=False)


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def check_notes(check: Check, game: Game, outcome: Outcome) -> tuple[bool, list[str]]:
    """(verified, notes) for a card. Nothing is said before the game has settled."""
    if outcome not in _CHECKED_OUTCOMES:
        return False, []
    notes = []
    for c in check.corrections:
        source = ("the official stats have" if c.source is DataSource.NFLVERSE
                  else "a later box score has")
        was = "no stat line" if c.old_value is None else _fmt(c.old_value)
        notes.append(f"Corrected after the game: {source} {_fmt(c.new_value)}; the live feed "
                     f"had {was}.")
    if check.verified:
        notes.append("Verified against the official stats (nflverse).")
    elif not check.checkable:
        notes.append("The official stats don't include this stat, so it can't be verified.")
    elif game.verified_at is None:
        notes.append("Awaiting verification against the official stats.")
    else:
        notes.append("The official stats don't confirm this number, so it isn't verified.")
    return check.verified, notes


def player_check(market: Market, game: Game, rows: dict[Stat, LiveStat],
                 corrections: Sequence[StatCorrection]) -> Check:
    stat = market.stat
    if stat is None or market.first_td or stat not in CHECKED_STATS | MAX_STATS:
        return UNCHECKED  # the longest plays are checked against the play-by-play
    mine = tuple(c for c in corrections if c.stat is stat)
    if not rows:  # no stat line: confirmed once nflverse, checked, had none either
        return Check(True, game.verified_at is not None, mine)
    row = rows.get(stat)
    return Check(True, row is not None and row.verified_at is not None, mine)


# --- The play that decided it -------------------------------------------------------------------

_CASHED = frozenset({Outcome.WON, Outcome.LOCKED})
_FIRST_TD_DECIDED = frozenset({Outcome.WON, Outcome.LOCKED, Outcome.LOST, Outcome.GONE})


def _decided(row: DecidingPlay | Play) -> dict:
    if isinstance(row, Play):  # the game's first touchdown: ESPN lists scoring plays exactly
        return {"text": row.label, "period": row.period, "clock": row.clock, "exact": True}
    return {"text": row.text, "period": row.period, "clock": row.clock, "exact": row.exact}


def deciding_plays(session: Session, game_id: int, athlete: str
                   ) -> dict[tuple[Stat, Decimal], DecidingPlay]:
    """By (stat, line); the exact play wins over the live feed's "on or around"."""
    out: dict[tuple[Stat, Decimal], DecidingPlay] = {}
    for row in session.scalars(select(DecidingPlay).where(
            DecidingPlay.game_id == game_id, DecidingPlay.espn_athlete_id == athlete)
            .order_by(DecidingPlay.exact)):
        out[(row.stat, row.line)] = row
    return out


def _build(market: Market, sel: Selection, bet: Bet, game: Game, stat: PlayerStat | None,
           now: datetime, focus: Focus, side: str | None = None,
           claim_seen: bool = True, check: Check = UNCHECKED,
           deciding_play: DecidingPlay | Play | None = None) -> CardView:
    decided = card.decide(bet, _state(game), stat, now)
    outcome = decided.outcome
    touched = plays.touches(market, focus.play)
    named = focus.market_key is not None and (
        focus.market_key == market.key or focus.market_key == market.key.replace(
            "_alternate", ""))
    if (not claim_seen and touched and outcome is Outcome.LIVE
            and game.status is not EventStatus.FINAL):
        outcome = Outcome.WAITING_FOR_FEED
    outcome_text = OUTCOME_TEXT[outcome]
    if outcome is Outcome.UNTRACKED and bet.line is None and market.tracked:
        outcome_text = NO_LINE_TEXT
    verified, notes = check_notes(check, game, outcome)
    shown = _FIRST_TD_DECIDED if market.first_td else _CASHED
    decided_by = (_decided(deciding_play) if deciding_play is not None and outcome in shown
                  else None)
    return CardView(
        key=market.key, label=market.label, bet=_bet_text(market, bet.line, side),
        line=bet.line, outcome=outcome, outcome_text=outcome_text,
        current=decided.current, needed=decided.needed,
        price=_price_dict(sel.quote) if sel.quote else None,
        no_price=NO_PRICE_TEXT[sel.reason] if sel.reason else None,
        others=_other_dicts(sel), touched=touched, named=named, alternate=market.alternate,
        verified=verified, decided_by=decided_by, notes=notes, **_money_fields(sel, outcome))


# --- Player -----------------------------------------------------------------------------------


def first_td_play(session: Session, game_id: int) -> Play | None:
    return session.scalars(select(Play).where(
        Play.game_id == game_id, Play.kind == PlayKind.TOUCHDOWN.value, Play.scoring.is_(True))
        .order_by(Play.period, Play.sequence).limit(1)).first()


def player_rows(session: Session, game_id: int, athlete: str) -> dict[Stat, LiveStat]:
    return {r.stat: r for r in session.scalars(select(LiveStat).where(
        LiveStat.game_id == game_id, LiveStat.espn_athlete_id == athlete))}


@dataclass
class PlayerView:
    game: GameView
    player: dict
    cards: list[CardView]
    waiting_for_feed: bool


_YES_NAMES = {YES, OVER}  # a yes-only market may come back as "Yes" or as "Over 0.5"
_OPPOSITE = {OVER: UNDER, YES: NO}


def player_offers(market: Market, rows: list[_Row], threshold: Decimal | None) -> list[Offer]:
    """The offers a player card prices: an alternate at its threshold, a Yes, or the Over."""
    if market.alternate:
        return _offers(rows, {OVER}, _OPPOSITE, threshold, True)
    if market.yes_only:
        return _offers(rows, _YES_NAMES, _OPPOSITE, None, False)
    return _offers(rows, {OVER}, _OPPOSITE, None, False)


def player_line(market: Market, quote: Quote | None, threshold: Decimal | None
                ) -> Decimal | None:
    """The line a player card is decided on: the price's own, 0.5 for a Yes, the threshold
    for a ladder, and none for the first touchdown."""
    if market.alternate:
        return threshold
    if market.yes_only:
        return None if market.first_td else Decimal("0.5")
    return quote.offer.point if quote is not None else None


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
    rows = player_rows(session, game.id, athlete)
    stats = {stat: r.value for stat, r in rows.items()}
    corrections = list(session.scalars(select(StatCorrection).where(
        StatCorrection.game_id == game.id, StatCorrection.espn_athlete_id == athlete)
        .order_by(StatCorrection.corrected_at, StatCorrection.id)))
    first_td = first_td_play(session, game.id)
    scorer = None if first_td is None else first_td.espn_athlete_id or card.UNKNOWN_SCORER
    decided = deciding_plays(session, game.id, athlete)
    claim_seen = claim_in_feed(focus, stats)
    views: list[CardView] = []
    for key in keys:
        market = BY_KEY.get(key)
        if market is None or market.scope is not Scope.PLAYER:
            continue
        if market.alternate and focus.threshold is None:
            continue  # ladders only when someone names a threshold
        offers = player_offers(market, rows_by_key[key], focus.threshold)
        sel = select_price(offers, listings.get(key, []), game.commence_time, chain)
        line = player_line(market, sel.quote, focus.threshold)
        value = stats.get(market.stat) if market.stat else None
        stat = PlayerStat(value, scorer, athlete)
        play: DecidingPlay | Play | None = None
        if market.first_td:
            play = first_td if scorer else None  # never an unattributed touchdown
        elif market.stat is not None and line is not None:
            play = decided.get((market.stat, line))
        views.append(_build(market, sel, Bet(market, line), game, stat, now, focus,
                            claim_seen=claim_seen,
                            check=player_check(market, game, rows, corrections),
                            deciding_play=play))
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


def team_offers(market: Market, rows: list[_Row], name: str, other_team: str) -> list[Offer]:
    """A side's moneyline or spread, its team total's Over, or the game total's Over."""
    if market.scope in (Scope.MONEYLINE, Scope.SPREAD):
        return _offers(rows, {name}, {name: other_team}, None, False)
    if market.scope is Scope.TEAM:
        return _offers([r for r in rows if r.price.description == name], {OVER}, _OPPOSITE,
                       None, False)
    return _offers(rows, {OVER}, _OPPOSITE, None, False)


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
        offers = team_offers(market, by_key[key], name, other_team)
        sel = select_price(offers, listings.get(key, []), game.commence_time, chain)
        line = sel.quote.offer.point if sel.quote else None
        bet = Bet(market, line, side_is_home=is_home)
        side = (game.home_abbr if is_home else game.away_abbr) or name
        # The final score is checked with the rest of the game; a disputed one never is.
        check = Check(True, game.verified_at is not None)
        views.append(_build(market, sel, bet, game, None, now, focus, side, check=check))
    return TeamView(game_view(game, now),
                    {"espn_id": team_espn_id, "name": name,
                     "abbr": game.home_abbr if is_home else game.away_abbr}, views)


# --- Parlay (M4) ------------------------------------------------------------------------------
#
# A parlay card shows each leg's price, at the parlay's book, in the same shape as a single
# card's: the owner allowed it on 2026-09-30 (docs/licensing.md). It is the one response that
# spans players, and only the 2 to 6 legs someone picked, in one game.


@dataclass
class LegView:
    key: str             # the leg as written in the URL
    kind: str            # "player" or "team"
    id: str              # ESPN athlete or team id
    who: str
    label: str
    bet: str
    line: Decimal | None
    outcome: Outcome
    outcome_text: str
    current: Decimal | None
    needed: Decimal | None
    price: dict | None   # as on a single card: american, book, point, when, provenance
    no_price: str | None


@dataclass
class ParlayView:
    game: GameView
    legs: list[LegView]
    outcome: Outcome
    outcome_text: str
    headline: str
    returns: Decimal | None
    fair_returns: Decimal | None
    book_name: str | None      # None: no single book priced every leg
    legs_counted: int
    notes: list[str]


@dataclass
class _Leg:
    key: parlay.LegKey
    kind: str
    id: str
    who: str
    sel: Selection
    stat: PlayerStat | None
    side: str | None = None
    side_is_home: bool | None = None


def parlay_view(session: Session, game: Game, legs: Sequence[parlay.LegKey], now: datetime,
                chain: Sequence[str]) -> ParlayView | None:
    """None when a leg names a player or team who isn't in this game."""
    first_td = first_td_play(session, game.id)
    scorer = None if first_td is None else first_td.espn_athlete_id or card.UNKNOWN_SCORER
    teams = {game.home_espn_id: (game.home_team, game.home_abbr, True),
             game.away_espn_id: (game.away_team, game.away_abbr, False)}
    built: list[_Leg] = []
    for leg in legs:
        key = leg.market.key
        listings = _listings(session, game.id, [key]).get(key, [])
        if leg.athlete is not None:
            player = session.get(Player, leg.athlete)
            if player is None or player.team_espn_id not in teams:
                return None
            offers = player_offers(leg.market, _price_rows(session, game.id, [key], leg.athlete),
                                   leg.threshold)
            rows = player_rows(session, game.id, leg.athlete)
            value = rows[leg.market.stat].value if leg.market.stat in rows else None
            built.append(_Leg(leg, "player", leg.athlete, player.name,
                              select_price(offers, listings, game.commence_time, chain),
                              PlayerStat(value, scorer, leg.athlete)))
        else:
            if leg.team not in teams:
                return None
            name, abbr, is_home = teams[leg.team]
            other = game.away_team if is_home else game.home_team
            offers = team_offers(leg.market, _price_rows(session, game.id, [key]), name, other)
            built.append(_Leg(leg, "team", leg.team or "", name,
                              select_price(offers, listings, game.commence_time, chain), None,
                              abbr or name, is_home))

    book, quotes = parlay.choose_book([b.sel for b in built], chain)
    state = _state(game)
    views: list[LegView] = []
    decided: list[tuple[Outcome, Quote | None]] = []
    for b, quote in zip(built, quotes, strict=True):
        market = b.key.market
        line = (player_line(market, quote, b.key.threshold) if b.kind == "player"
                else quote.offer.point if quote is not None else None)
        outcome = card.decide(Bet(market, line, b.side_is_home), state, b.stat, now)
        decided.append((outcome.outcome, quote))
        views.append(LegView(
            key=b.key.text, kind=b.kind, id=b.id, who=b.who, label=market.label,
            bet=_bet_text(market, line, b.side), line=line, outcome=outcome.outcome,
            outcome_text=(NO_LINE_TEXT if quote is None and market.tracked
                          and outcome.outcome is Outcome.UNTRACKED
                          else OUTCOME_TEXT[outcome.outcome]),
            current=outcome.current,
            needed=outcome.needed,
            price=_price_dict(quote) if quote else None,
            no_price=None if quote else NO_PRICE_TEXT[b.sel.reason or NoPrice.NOT_LISTED]))

    result = parlay.parlay_outcome([o for o, _ in decided])
    notes = [parlay.SGP_NOTE]
    priced = [(o, q) for o, q in decided if q is not None]
    if len(priced) < len(decided):
        return ParlayView(game_view(game, now), views, result, OUTCOME_TEXT[result],
                          "No price on file for every leg", None, None, None,
                          0, notes)
    if book is None:
        notes.append(parlay.MIXED_BOOKS_NOTE)
    m = parlay.money(priced)
    back = parlay.settled_returns(result, m)
    if result in (Outcome.WON, Outcome.LOCKED):
        headline = f"$10 → ${m.returns}"
    elif back is not None:
        headline = f"$10 → ${back}"
    else:
        headline = f"$10 → ${m.returns} if it hits"
    return ParlayView(game_view(game, now), views, result, OUTCOME_TEXT[result], headline,
                      m.returns, m.fair_returns,
                      BOOK_NAMES.get(book, book) if book else None, m.legs_counted, notes)
