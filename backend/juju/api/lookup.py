"""Which player or team a lookup means (docs/GOALS.md section 7.3).

Players are looked for in context order: games in play, then games around today, then the
last week. A clear match (90 or more, nobody else within 3 points) goes straight to the result.
Anything less gives the person candidates to tap. Teams are matched by their aliases
(parlaytracker's rules) when no player is named.
"""
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.core.enums import EventStatus, PlayKind
from juju.core.markets import BY_KEY, Scope
from juju.core.models import Game, Player
from juju.core.parlay import MAX_LEGS, LegKey
from juju.ingest.llm import LlmReader
from juju.ingest.parse import Parsed, for_position, parse_text
from juju.ingest.resolve import (
    RosterEntry, is_confident, mentions, normalize_name, rank_players, team_aliases,
)

IN_PLAY = (EventStatus.IN_PROGRESS, EventStatus.BREAK, EventStatus.DELAYED)


@dataclass(frozen=True)
class PoolEntry:
    entry: RosterEntry
    game: Game


def game_tiers(session: Session, now: datetime) -> list[list[Game]]:
    """Games in play; games from 18 hours ago to 12 hours ahead; the rest of the last week."""
    games = session.scalars(select(Game).where(
        Game.commence_time.between(now - timedelta(days=7), now + timedelta(hours=12)))
        .order_by(Game.commence_time.desc())).all()
    live = [g for g in games if g.status in IN_PLAY]
    near = [g for g in games if g not in live
            and now - timedelta(hours=18) <= g.commence_time <= now + timedelta(hours=12)]
    week = [g for g in games if g not in live and g not in near]
    return [live, near, week]


def pool(session: Session, games: list[Game]) -> list[PoolEntry]:
    """The players of `games`. A player in two games counts for the most recent one."""
    by_team: dict[str, Game] = {}
    for g in sorted(games, key=lambda g: g.commence_time):
        for t in (g.home_espn_id, g.away_espn_id):
            if t:
                by_team[t] = g
    if not by_team:
        return []
    players = session.scalars(select(Player).where(Player.team_espn_id.in_(by_team))).all()
    return [PoolEntry(RosterEntry(p.espn_athlete_id, p.name, p.team_espn_id, p.unavailable),
                      by_team[p.team_espn_id]) for p in players if p.team_espn_id]


@dataclass
class Choice:
    kind: str            # "player" or "team"
    game_id: int
    id: str              # ESPN athlete id or team id
    name: str
    detail: str          # "PHI · QB · PHI @ CHI"


@dataclass
class Result:
    kind: str            # "player", "team", "parlay", "choices", "none"
    path: str            # "deterministic", "llm", "choices", "none"
    parsed: Parsed
    game_id: int | None = None
    id: str | None = None
    choices: list[Choice] = field(default_factory=list)
    message: str | None = None
    legs: str | None = None  # a parlay's legs, as its URL writes them

    @property
    def expect(self) -> bool:
        """They described something that happened, so it should already count."""
        return self.parsed.play is not None and self.parsed.play is not PlayKind.UNKNOWN


def _team_abbr(game: Game, team: str | None) -> str | None:
    if team == game.home_espn_id:
        return game.home_abbr
    if team == game.away_espn_id:
        return game.away_abbr
    return None


def _player_choice(p: PoolEntry, position: dict[str, str]) -> Choice:
    abbr = _team_abbr(p.game, p.entry.team_espn_id) or ""
    pos = position.get(p.entry.espn_athlete_id, "")
    detail = " · ".join(x for x in (abbr, pos, p.game.label) if x)
    return Choice("player", p.game.id, p.entry.espn_athlete_id, p.entry.name, detail)


def _find_team(session: Session, text: str, tiers: list[list[Game]]) -> list[Choice]:
    norm = normalize_name(text)
    for games in tiers:
        hits = []
        for g in games:
            home = team_aliases(g.home_team, g.home_abbr)
            away = team_aliases(g.away_team, g.away_abbr)
            for aliases, team, name in ((home - away, g.home_espn_id, g.home_team),
                                        (away - home, g.away_espn_id, g.away_team)):
                if team and mentions(norm, aliases):
                    hits.append(Choice("team", g.id, team, name, g.label))
        if hits:
            return hits
    return []


# "Barkley TD and Smith 5+ catches": parts joined by "and", "&", a comma or a spaced "+".
# ("100+" has no space before its "+", so it never splits.)
_PARTS = re.compile(r"\s*(?:,|;|&|\band\b|\s\+\s)\s*", re.IGNORECASE)
_TEAM_MARKETS = frozenset({"h2h", "spreads", "totals", "team_totals"})
_PLAY_MARKETS = {PlayKind.TOUCHDOWN: "player_anytime_td", PlayKind.PASS_TD: "player_pass_tds"}


def _leg(result: Result) -> LegKey | None:
    """The bet one part of a parlay names, or None if it names none."""
    p = result.parsed
    if result.kind == "team":
        if p.market_key in _TEAM_MARKETS:
            return LegKey(BY_KEY[p.market_key], team=result.id)
        return None
    key = p.market_key or (_PLAY_MARKETS.get(p.play) if p.play else None)
    market = BY_KEY.get(key) if key else None
    if market is None or market.scope is not Scope.PLAYER:
        return None
    ladder = BY_KEY.get(f"{market.key}_alternate")
    if p.threshold is not None and ladder is not None:
        return LegKey(ladder, athlete=result.id, threshold=p.threshold)  # the line they named
    return LegKey(market, athlete=result.id)


def resolve_parlay(session: Session, text: str, now: datetime) -> Result | None:
    """Several bets in one line become a parlay only if every part is certain: one player
    or team each, in the same game, each naming a bet. Anything less is read as one lookup.
    Parts are read deterministically: the LLM is never asked about each one."""
    parts = [x for x in _PARTS.split(text) if x.strip()]
    if not 2 <= len(parts) <= MAX_LEGS:
        return None
    games, legs = set(), []
    for part in parts:
        r = resolve(session, part, now, None, parts_allowed=False)
        leg = _leg(r) if r.kind in ("player", "team") else None
        if leg is None:
            return None
        games.add(r.game_id)
        legs.append(leg.text)
    if len(games) != 1 or len(set(legs)) != len(legs):
        return None
    return Result("parlay", "deterministic", parse_text(text), games.pop(),
                  legs=",".join(legs))


def resolve(session: Session, text: str, now: datetime, llm: LlmReader | None = None,
            parts_allowed: bool = True) -> Result:
    if parts_allowed and (several := resolve_parlay(session, text, now)) is not None:
        return several
    parsed = parse_text(text)
    tiers = game_tiers(session, now)
    positions: dict[str, str] = {}

    def search(name_text: str) -> Result | None:
        if not name_text:
            return None
        for games in tiers:
            entries = pool(session, games)
            if not entries:
                continue
            ranked = rank_players(name_text, [e.entry for e in entries])
            if not ranked:
                continue
            by_id = {e.entry.espn_athlete_id: e for e in entries}
            if not positions:
                positions.update(dict(session.execute(
                    select(Player.espn_athlete_id, Player.position).where(
                        Player.espn_athlete_id.in_([c.entry.espn_athlete_id
                                                    for c in ranked]))).all()))
            if is_confident(ranked):
                top = by_id[ranked[0].entry.espn_athlete_id]
                return Result("player", "deterministic", parsed, top.game.id,
                              top.entry.espn_athlete_id)
            return Result("choices", "choices", parsed, choices=[
                _player_choice(by_id[c.entry.espn_athlete_id], positions) for c in ranked])
        return None

    def from_his_side(result: Result) -> Result:
        """The play as the named player saw it (a defender's interception is his)."""
        if result.kind == "player" and result.id is not None:
            position = positions.get(result.id) or session.scalar(
                select(Player.position).where(Player.espn_athlete_id == result.id))
            play = for_position(result.parsed.play, position)
            if play is not result.parsed.play:
                result.parsed = replace(result.parsed, play=play)
        return result

    found = search(parsed.name_text)
    if found is not None and found.kind == "player":
        return from_his_side(found)
    if found is None:
        teams = _find_team(session, parsed.text, tiers)
        if len(teams) == 1:
            return Result("team", "deterministic", parsed, teams[0].game_id, teams[0].id)
        if teams:
            return Result("choices", "choices", parsed, choices=teams)
    if llm is not None:
        reading = llm.read(text)
        if reading and reading.player_name:
            again = search(normalize_name(reading.player_name))
            if again is not None:
                if reading.play and parsed.play is None:
                    again.parsed = Parsed(parsed.text, reading.play, reading.yards,
                                          parsed.threshold, parsed.market_key,
                                          parsed.name_text)
                again.path = "llm" if again.kind == "player" else "choices"
                return from_his_side(again)
    if found is not None:
        return found
    return Result("none", "none", parsed,
                  message="We couldn't tell which player or team you mean. Try a name, "
                          "like \"Barkley TD\", or pick from Live now.")


def suggest(session: Session, q: str, now: datetime, limit: int = 8) -> list[Choice]:
    """Typeahead: players in play first, then around today, then the last week."""
    key = normalize_name(q)
    if len(key) < 2:
        return []
    out: list[Choice] = []
    seen: set[str] = set()
    for games in game_tiers(session, now):
        entries = pool(session, games)
        positions = {}
        matches = []
        for e in entries:
            words = normalize_name(e.entry.name).split()
            name = " ".join(words)
            if name.startswith(key) or any(w.startswith(key) for w in words[1:]):
                matches.append(e)
        if matches:
            positions = dict(session.execute(select(Player.espn_athlete_id, Player.position)
                             .where(Player.espn_athlete_id.in_(
                                 [m.entry.espn_athlete_id for m in matches]))).all())
        for e in sorted(matches, key=lambda e: (e.entry.unavailable, e.entry.name)):
            if e.entry.espn_athlete_id in seen:
                continue
            seen.add(e.entry.espn_athlete_id)
            out.append(_player_choice(e, positions))
            if len(out) >= limit:
                return out
    return out
