"""Which player or team a lookup means (docs/GOALS.md section 7.3).

Players are looked for in context order: games in play (and games final within the hour), then
games around today, then the last week. A clear match (90 or more, nobody else within 3 points)
goes straight to the result. Among namesakes, one who made a play of the kind described in the
last ten minutes is the one meant: picked if he is the only one, listed first otherwise.
Anything less gives the person candidates to tap. Teams are matched by their aliases
(parlaytracker's rules, plus the common nicknames) when no player is named. Text that names no
one ("what just happened", "that TD") gets the latest plays to tap, newest first: with ten games
on, picking one would be a guess.
"""
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from juju.core.enums import EventStatus, PlayKind
from juju.core.live import period_label
from juju.core.markets import BY_KEY, Scope
from juju.core.models import Game, Play, Player
from juju.core.parlay import MAX_LEGS, LegKey
from juju.ingest.llm import LlmReader
from juju.ingest.parse import Parsed, for_position, parse_text
from juju.ingest.resolve import (
    PLAYER_AUTO_SCORE, Candidate, RosterEntry, is_confident, mentions, normalize_name,
    rank_players, team_aliases,
)

IN_PLAY = (EventStatus.IN_PROGRESS, EventStatus.BREAK, EventStatus.DELAYED)
JUST_FINAL = timedelta(hours=1)   # a game this recently over is still "the one on"
RECENT_PLAY = timedelta(minutes=10)
LATEST_PLAYS = 8

# What people call teams beyond their names (normalised display name -> nicknames). "Birds"
# is the Eagles'; Atlanta's "Dirty Birds" would make it ambiguous, so it isn't listed.
NICKNAMES: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in {
    "philadelphia eagles": {"philly", "birds", "iggles"},
    "san francisco 49ers": {"niners", "sf"},
    "tampa bay buccaneers": {"bucs"},
    "new england patriots": {"pats"},
    "jacksonville jaguars": {"jags"},
    "green bay packers": {"pack"},
    "new york giants": {"g men", "gmen", "big blue"},
    "new york jets": {"gang green"},
    "kansas city chiefs": {"kc"},
    "los angeles chargers": {"bolts"},
    "las vegas raiders": {"vegas"},
    "minnesota vikings": {"vikes"},
    "cincinnati bengals": {"cincy"},
    "miami dolphins": {"fins", "phins"},
    "seattle seahawks": {"hawks"},
    "indianapolis colts": {"indy"},
    "arizona cardinals": {"cards"},
    "chicago bears": {"da bears"},
    "washington commanders": {"commanders", "dc"},
    "los angeles rams": {"la rams"},
}.items()}
# Team abbreviations that are also everyday words: a team only when they are the whole text.
WORDY_ABBREVIATIONS = frozenset({"no", "ten", "car", "min", "den", "sea", "was", "ne", "ind",
                                 "lac", "lar"})


def in_play_or_just_final(game: Game, now: datetime) -> bool:
    return game.status in IN_PLAY or (
        game.status is EventStatus.FINAL and game.final_at is not None
        and now - game.final_at <= JUST_FINAL)


@dataclass(frozen=True)
class PoolEntry:
    entry: RosterEntry
    game: Game


def game_tiers(session: Session, now: datetime) -> list[list[Game]]:
    """Games in play or final within the hour; games from 18 hours ago to 12 hours ahead; the
    rest of the last week."""
    games = session.scalars(select(Game).where(
        Game.commence_time.between(now - timedelta(days=7), now + timedelta(hours=12)))
        .order_by(Game.commence_time.desc())).all()
    live = [g for g in games if in_play_or_just_final(g, now)]
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
    kind: str            # "player" or "team": the page it opens
    game_id: int
    id: str              # ESPN athlete id or team id
    name: str            # a play's label, for a play
    detail: str          # "PHI · QB · PHI @ CHI", or "Q2 3:12 · PHI @ CHI" for a play
    play_id: int | None = None  # a play from the latest plays: the page names it
    play: str | None = None     # the play's kind from this player's side (a PlayKind value)
    yards: int | None = None


@dataclass
class Result:
    kind: str            # "player", "team", "parlay", "choices", "plays", "none"
    path: str            # "deterministic", "recent", "llm", "choices", "plays", "none"
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


def aliases_for(name: str, abbreviation: str | None, text: str) -> frozenset[str]:
    """A team's aliases with its nicknames, less abbreviations that are everyday words (unless
    the text is nothing but that word)."""
    aliases = team_aliases(name, abbreviation) | NICKNAMES.get(normalize_name(name), frozenset())
    return frozenset(a for a in aliases if a not in WORDY_ABBREVIATIONS or a == text)


def _find_team(session: Session, text: str, tiers: list[list[Game]]) -> list[Choice]:
    norm = normalize_name(text)
    for games in tiers:
        hits = []
        for g in games:
            home = aliases_for(g.home_team, g.home_abbr, norm)
            away = aliases_for(g.away_team, g.away_abbr, norm)
            for aliases, team, name in ((home - away, g.home_espn_id, g.home_team),
                                        (away - home, g.away_espn_id, g.away_team)):
                if team and mentions(norm, aliases):
                    hits.append(Choice("team", g.id, team, name, g.label))
        if hits:
            return hits
    return []


# --- Plays: the latest ones, and who just made one --------------------------------------------

# A described play -> the stored plays it can be: (stored kind, whose: the player or the passer).
_PLAY_ROWS: dict[PlayKind, tuple[tuple[str, str], ...]] = {
    PlayKind.TOUCHDOWN: (("touchdown", "player"),),
    PlayKind.PASS_TD: (("touchdown", "passer"),),
    PlayKind.RUN: (("run", "player"), ("touchdown", "player")),
    PlayKind.CATCH: (("catch", "player"), ("touchdown", "player")),
    PlayKind.PASS: (("catch", "passer"), ("touchdown", "passer")),
    PlayKind.FIELD_GOAL: (("field_goal", "player"),),
    PlayKind.INTERCEPTION: (("def_interception", "player"), ("def_interception", "passer")),
    PlayKind.DEF_INTERCEPTION: (("def_interception", "player"),),
}
# Unnamed and untyped ("what just happened"): the player's side of every play, and the
# thrower's side of a touchdown pass.
_ANY: tuple[tuple[str, str], ...] = tuple(
    (kind, "player") for kind in ("touchdown", "run", "catch", "field_goal", "def_interception")
) + (("touchdown", "passer"),)
# What the play was from the passer's side.
_THROWN = {"touchdown": PlayKind.PASS_TD, "catch": PlayKind.PASS,
           "def_interception": PlayKind.INTERCEPTION}


def _sides(play: Play, wanted: PlayKind | None, yards: int | None) -> list[str]:
    """Whose play this is, for what was described: "player", "passer", both or neither."""
    if yards is not None and (play.yards is None or abs(play.yards - yards) > 5):
        return []
    rows = _ANY if wanted in (None, PlayKind.UNKNOWN) else _PLAY_ROWS.get(wanted, ())
    return [side for kind, side in rows if kind == play.kind
            and (side == "player" or play.passer_espn_id is not None)]


def recent_players(session: Session, ids: list[str], wanted: PlayKind | None, now: datetime
                   ) -> list[str]:
    """Of `ids`, the players who made a play of the kind described in the last ten minutes
    (or threw it, for a pass), most recent first."""
    rows = session.scalars(select(Play).where(
        or_(Play.espn_athlete_id.in_(ids), Play.passer_espn_id.in_(ids)),
        Play.wallclock >= now - RECENT_PLAY).order_by(Play.wallclock.desc())).all()
    out: list[str] = []
    for p in rows:
        for side in _sides(p, wanted, None):
            who = p.espn_athlete_id if side == "player" else p.passer_espn_id
            if who in ids and who not in out:
                out.append(who)
    return out


def _short(name: str | None) -> str:
    if not name:
        return "Team"
    first, _, last = name.partition(" ")
    return f"{first[:1]}. {last}" if last else name


def latest_plays(session: Session, parsed: Parsed, now: datetime,
                 limit: int = LATEST_PLAYS) -> list[Choice]:
    """The newest notable plays in games in play or just over, filtered by the kind of play and
    the yards described; a pass is also offered as its thrower's. No prices."""
    games = {g.id: g for g in session.scalars(select(Game).where(
        Game.commence_time.between(now - timedelta(hours=14), now + timedelta(hours=1))))
        if in_play_or_just_final(g, now)}
    if not games:
        return []
    rows = session.scalars(select(Play).where(Play.game_id.in_(games))
                           .order_by(Play.wallclock.desc().nulls_last(), Play.id.desc())
                           .limit(80)).all()
    passers = {p.espn_athlete_id: p.name for p in session.scalars(select(Player).where(
        Player.espn_athlete_id.in_({r.passer_espn_id for r in rows if r.passer_espn_id})))}
    out: list[Choice] = []
    for p in rows:
        g = games[p.game_id]
        when = " ".join(x for x in (period_label(p.period), p.clock) if x)
        detail = " · ".join(x for x in (when, g.label) if x)
        for side in _sides(p, parsed.play, parsed.yards):
            if side == "passer":
                assert p.passer_espn_id is not None
                thrown = _THROWN[p.kind]
                yards = f"{p.yards}-yd " if p.yards is not None and thrown is not \
                    PlayKind.INTERCEPTION else ""
                what = {PlayKind.PASS_TD: "TD pass", PlayKind.PASS: "completion",
                        PlayKind.INTERCEPTION: "interception thrown"}[thrown]
                out.append(Choice("player", g.id, p.passer_espn_id,
                                  f"{_short(passers.get(p.passer_espn_id))} {yards}{what}",
                                  detail, p.id, thrown.value, p.yards))
            elif p.espn_athlete_id:
                out.append(Choice("player", g.id, p.espn_athlete_id, p.label, detail, p.id,
                                  p.kind, p.yards))
            elif p.scoring and p.team_espn_id:  # a defense's or a team's score, no one named
                out.append(Choice("team", g.id, p.team_espn_id, p.label, detail, p.id, p.kind,
                                  p.yards))
        if len(out) >= limit:
            break
    return out[:limit]


def _the_one_who_just_did_it(session: Session, ranked: list[Candidate], parsed: Parsed,
                             now: datetime) -> tuple[str | None, list[Candidate]]:
    """Among namesakes (each a strong name match), the one who just made the play described:
    (him, if he is the only one; the candidates with recent players first)."""
    strong = [c.entry.espn_athlete_id for c in ranked if c.score >= PLAYER_AUTO_SCORE]
    recent = recent_players(session, strong, parsed.play, now) if len(strong) > 1 else []
    order = {a: i for i, a in enumerate(recent)}
    first = sorted(ranked, key=lambda c: order.get(c.entry.espn_athlete_id, len(order)))
    return (recent[0] if len(recent) == 1 else None), first


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
            just, ranked = _the_one_who_just_did_it(session, ranked, parsed, now)
            if just is not None:
                return Result("player", "recent", parsed, by_id[just].game.id, just)
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
    if llm is not None and (found is not None or parsed.name_text):
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
    if plays := latest_plays(session, parsed, now):
        return Result("plays", "plays", parsed, choices=plays,
                      message="Which play? The latest, newest first.")
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
