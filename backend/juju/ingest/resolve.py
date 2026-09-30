"""Name matching, after parlaytracker@c3bd43c parlaytracker/ingest/resolve.py.

`normalize_name`, the team alias rules, `same_game`, the player key and the doubt thresholds are
unchanged. Added: `match_abbreviated` for ESPN's play-by-play names ("J.Hurts"), and
`rank_players`, the roster-index search the free-text parser uses.

Anything ambiguous is reported as doubtful, never chosen silently.
"""
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz import fuzz

EVENT_TIME_TOLERANCE = timedelta(hours=3)
PLAYER_AUTO_SCORE = 90      # select automatically
PLAYER_DOUBTFUL_SCORE = 75  # 75-89: offer, but ask; below: not a candidate
AMBIGUOUS_MARGIN = 3        # a runner-up this close makes even a good match doubtful
SURNAME_SCORE = 95.0        # the surname alone matched exactly

# The Odds API's team names against ESPN's displayName. They match today (verified by
# parlaytracker for the NFL); record any team that differs here, in normalised form.
TEAM_ALIASES: dict[str, str] = {}


def normalize_name(name: str) -> str:
    """Lowercase, ASCII, no punctuation: 'D'Andre Swift' -> 'dandre swift'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", "", text.lower().replace("-", " ")).strip()


def _team_key(name: str) -> str:
    key = normalize_name(name)
    return TEAM_ALIASES.get(key, key)


def same_team(a: str, b: str) -> bool:
    return _team_key(a) == _team_key(b)


def same_game(home: str, away: str, start: datetime, other_home: str, other_away: str,
              other_start: datetime) -> bool:
    """Both teams match, and the start times are within 3 hours."""
    return (same_team(home, other_home) and same_team(away, other_away)
            and abs(start - other_start) <= EVENT_TIME_TOLERANCE)


_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def player_key(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIXES.sub("", normalize_name(name))).strip()


# --- Teams ------------------------------------------------------------------------------------

# First words that many teams share: never an alias by themselves.
GENERIC_WORDS = frozenset({"new", "los", "san", "las", "st", "saint", "golden", "north", "south",
                           "west", "east", "tampa"})


def team_aliases(display_name: str, abbreviation: str | None) -> frozenset[str]:
    """Everything a person might call a team: "Chicago Bears" -> chicago bears, chicago,
    bears, chi."""
    words = normalize_name(display_name).split()
    aliases = {" ".join(words)}
    if abbreviation:
        aliases.add(normalize_name(abbreviation))
    for n in range(1, len(words)):
        location, nickname = " ".join(words[:n]), " ".join(words[n:])
        if location not in GENERIC_WORDS:
            aliases.add(location)
        aliases.add(nickname)
    return frozenset(a for a in aliases if a)


def mentions(text_norm: str, aliases: Iterable[str]) -> bool:
    padded = f" {text_norm} "
    return any(f" {a} " in padded for a in aliases)


# --- Players ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterEntry:
    espn_athlete_id: str
    name: str
    team_espn_id: str | None = None
    unavailable: bool = False


@dataclass(frozen=True)
class PlayerMatch:
    espn_athlete_id: str | None
    name: str | None = None
    score: float = 0.0
    doubtful: bool = False


def match_roster_player(name: str | None, roster: Iterable[RosterEntry]) -> PlayerMatch:
    """Match a printed name against a game's rosters (parlaytracker's rule): 90 or above
    selects automatically, 75-89 is doubtful, below 75 is no match. A runner-up within a few
    points (two players named alike) also makes the match doubtful."""
    roster = list(roster)
    if not name or not roster:
        return PlayerMatch(None)
    key = player_key(name)
    scored = sorted(((fuzz.token_sort_ratio(key, player_key(p.name)), p) for p in roster),
                    key=lambda sp: (-sp[0], sp[1].unavailable, sp[1].name))
    best_score, best = scored[0]
    if best_score < PLAYER_DOUBTFUL_SCORE:
        return PlayerMatch(None)
    runner_up = next((s for s, p in scored[1:] if p.espn_athlete_id != best.espn_athlete_id), 0)
    doubtful = best_score < PLAYER_AUTO_SCORE or best_score - runner_up < AMBIGUOUS_MARGIN
    return PlayerMatch(best.espn_athlete_id, best.name, best_score, doubtful)


_ABBREVIATED = re.compile(r"^(?P<initials>[a-z]+)\s?(?P<last>[a-z].*)$")


def match_abbreviated(name: str | None, roster: Iterable[RosterEntry]) -> str | None:
    """ESPN's play-by-play name ("J.Hurts", "A.St. Brown") -> the one roster player whose first
    name starts with the initial and whose last name matches. None unless exactly one fits."""
    if not name or "." not in name:
        return None
    first, _, last = name.partition(".")
    initial = normalize_name(first)[:1]
    last_key = player_key(last)
    if not initial or not last_key:
        return None
    hits = []
    for p in roster:
        words = player_key(p.name).split()
        if len(words) < 2:
            continue
        target = last_key.replace(" ", "")
        if words[0].startswith(initial) and any(
                "".join(words[k:]) == target for k in range(1, len(words))):
            hits.append(p.espn_athlete_id)
    return hits[0] if len(set(hits)) == 1 else None


@dataclass(frozen=True)
class Candidate:
    entry: RosterEntry
    score: float


def rank_players(text: str, roster: Iterable[RosterEntry], limit: int = 5) -> list[Candidate]:
    """Players that `text` might name, best first. A full-name match scores on the whole name;
    a surname on its own ("Barkley TD") scores on the surname, so it matches exactly when only
    one player in the pool has it."""
    words = player_key(text).split()
    if not words:
        return []
    grams = {" ".join(words[i:j]) for i in range(len(words))
             for j in range(i + 1, min(len(words), i + 3) + 1)}
    best: dict[str, Candidate] = {}
    for p in roster:
        full = player_key(p.name)
        parts = full.split()
        last = " ".join(parts[1:]) if len(parts) > 1 else full
        score = 0.0
        for g in grams:
            if len(g) < 3:
                continue
            s = fuzz.token_sort_ratio(g, full)
            if g == last or (len(parts) > 1 and g == parts[-1]):
                # A surname on its own: strong, but a full name beats it, so "DeVonta Smith"
                # is clear even with another Smith in the game.
                s = max(s, SURNAME_SCORE)
            elif len(g) >= 4:
                s = max(s, fuzz.ratio(g, last) - 5)  # a typo in the surname costs a little
            score = max(score, s)
        if score >= PLAYER_DOUBTFUL_SCORE:
            held = best.get(p.espn_athlete_id)
            if held is None or score > held.score:
                best[p.espn_athlete_id] = Candidate(p, score)
    ranked = sorted(best.values(), key=lambda c: (-c.score, c.entry.unavailable, c.entry.name))
    return ranked[:limit]


def is_confident(candidates: list[Candidate]) -> bool:
    """One clear winner: at least 90, and nobody else within the ambiguity margin."""
    if not candidates or candidates[0].score < PLAYER_AUTO_SCORE:
        return False
    return len(candidates) == 1 or candidates[0].score - candidates[1].score >= AMBIGUOUS_MARGIN
