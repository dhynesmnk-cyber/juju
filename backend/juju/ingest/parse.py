"""Reading one line of free text, deterministically (docs/GOALS.md section 7.4).

This picks out the kind of play, the yards, a threshold ("100+", "over 72.5") and a market
named in words. Which player the text means is decided by `juju/api/lookup.py` against the
rosters of the games in play. Nothing here calls a model.
"""
import re
from dataclasses import dataclass, replace
from decimal import Decimal

from juju.core.enums import PlayKind
from juju.ingest.resolve import normalize_name

MAX_TEXT = 200


@dataclass(frozen=True)
class Parsed:
    text: str                   # normalised
    play: PlayKind | None
    yards: int | None
    threshold: Decimal | None   # an Over line: "100+" -> 99.5, "over 72.5" -> 72.5
    market_key: str | None
    name_text: str              # the text with play and market words removed


_WORDS = {
    "td": r"\b(td|tds|touchdowns?|scores?|scored|scoring|house|endzone|end zone|pick (six|6))\b",
    "throw": r"\b(throws?|threw|tosses|tossed|td pass|touchdown pass|passing td|pass td|"
             r"hits|finds|found)\b",
    "catch": r"\b(catch|catches|caught|reception|receptions|grab|grabs|rec|recs|snag)\b",
    "run": r"\b(run|runs|ran|rush|rushes|rushed|rushing|carry|carries|scramble|scrambles)\b",
    "pass": r"\b(pass|passes|passing|completion|completions|throw|bomb|dime)\b",
    "fg": r"\b(field goal|fg|fgs|kick|kicks|kicked|boots)\b",
    "int": r"\b(interception|interceptions|intercepted|int|ints|picked|pick|picks)\b",
    "sack": r"\b(sack|sacks|sacked)\b",
}
_RE = {k: re.compile(v) for k, v in _WORDS.items()}
_YARDS = re.compile(r"\b(\d{1,3})\s*(?:yd|yds|yard|yards|yarder)\b|\b(\d{1,3})\s*y\b")
_PLUS = re.compile(r"\b(\d{1,3})\s*\+")  # checked on the raw text: normalising drops "+"
_OVER = re.compile(r"\bover\s*(\d{1,3}(?:\.5)?)\b|\bo\s?(\d{1,3}\.5)\b")

# (pattern, market key). The first match wins, so specific wording comes first.
_MARKETS: list[tuple[re.Pattern[str], str]] = [(re.compile(p), k) for p, k in [
    (r"\b(first|1st) (td|touchdown)", "player_1st_td"),
    (r"\bany ?time\b", "player_anytime_td"),
    # A rushing or receiving touchdown is a touchdown, not yards (the rush and receiving TD
    # markets are never offered; the anytime TD is the bet people mean).
    (r"\b(rushing|rush|receiving|rec) (td|tds|touchdowns?)\b", "player_anytime_td"),
    (r"\blongest (rush|run|carry)", "player_rush_longest"),
    (r"\blongest (reception|catch|rec)", "player_reception_longest"),
    (r"\blongest (completion|pass)", "player_pass_longest_completion"),
    (r"\b(rec|receiving|reception) ?(yd|yds|yards)\b|\breceiving\b", "player_reception_yds"),
    (r"\b(rush|rushing) ?(yd|yds|yards)\b|\brushing\b", "player_rush_yds"),
    (r"\b(pass|passing) ?(yd|yds|yards)\b", "player_pass_yds"),
    (r"\b(pass|passing) (td|tds|touchdowns?)\b", "player_pass_tds"),
    (r"\b(receptions|catches|recs)\b", "player_receptions"),
    (r"\bcompletions\b", "player_pass_completions"),
    (r"\b(field goals|fgs)\b", "player_field_goals"),
    (r"\bteam totals?\b", "team_totals"),
    (r"\b(game total|total points|the over)\b", "totals"),
    (r"\b(spread|cover|covers|covering|ats)\b", "spreads"),
    (r"\b(moneyline|ml|to win)\b", "h2h"),
]]
_FILLER = re.compile(r"\b(a|an|the|for|on|of|and|with|just|what|would|have|paid|bet|yard|"
                     r"yards|yarder|yds|yd|over|under|from|to|in|at|by|he|his|big|huge|long|another|"
                     r"line|ladder|alt|alternate|milestone|plus|longest|game|total|points?|"
                     # Time and exclamations ("that TD", "who just scored", "what a play"): never
                     # a name, but four letters or more would be fuzzy-matched against surnames.
                     r"that|this|who|whos|last|latest|did|does|was|is|it|its|play|happened|"
                     r"happen|now|again|wow|great|crazy|nice|insane|unreal|amazing|omg)\b")


OFFENSE = frozenset({"QB", "RB", "FB", "WR", "TE"})
DEFENSE = frozenset({"DE", "DT", "NT", "LB", "OLB", "ILB", "MLB", "CB", "S", "FS", "SS", "DB"})


def for_position(play: PlayKind | None, position: str | None) -> PlayKind | None:
    """The play from the named player's side, once we know who he is: a defender's
    interception is one he made, and a receiver's "pass" or "TD pass" is his catch or score."""
    if play is PlayKind.INTERCEPTION and position in DEFENSE:
        return PlayKind.DEF_INTERCEPTION
    if position in OFFENSE and position != "QB":
        if play is PlayKind.PASS:
            return PlayKind.CATCH
        if play is PlayKind.PASS_TD:
            return PlayKind.TOUCHDOWN
    return play


def parse_text(text: str) -> Parsed:
    raw = (text or "")[:MAX_TEXT]
    norm = normalize_name(raw.replace("+", " plus "))
    has = {k: bool(r.search(norm)) for k, r in _RE.items()}
    play: PlayKind | None
    if has["td"] and has["throw"]:
        play = PlayKind.PASS_TD
    elif has["td"]:
        play = PlayKind.TOUCHDOWN
    elif has["int"]:
        play = PlayKind.INTERCEPTION
    elif has["fg"]:
        play = PlayKind.FIELD_GOAL
    elif has["sack"]:
        play = PlayKind.SACK
    elif has["catch"]:
        play = PlayKind.CATCH
    elif has["run"]:
        play = PlayKind.RUN
    elif has["pass"]:
        play = PlayKind.PASS
    else:
        play = None

    yards = None
    if m := _YARDS.search(norm):
        yards = int(m.group(1) or m.group(2))
    threshold = None
    if m := _PLUS.search(raw):
        threshold = Decimal(m.group(1)) - Decimal("0.5")
    elif m := _OVER.search(raw.lower()):
        threshold = Decimal(m.group(1) or m.group(2))
    market_key = next((k for p, k in _MARKETS if p.search(norm)), None)

    name_text = norm
    for r in list(_RE.values()) + [p for p, _ in _MARKETS]:
        name_text = r.sub(" ", name_text)
    name_text = _FILLER.sub(" ", name_text)
    name_text = re.sub(r"\b(plus|\d+(\.\d+)?)\b", " ", name_text)
    name_text = re.sub(r"\s+", " ", name_text).strip()
    return Parsed(norm, play, yards, threshold, market_key, name_text)


# --- The live chat's reading (docs/plans/live-chat.md) ---------------------------------------
#
# `parse_text` stays as it is: the archive's lookups depend on it. The live chat needs more of
# the line: which side (over or under), the number the words ask for ("4 catches" is Over 3.5,
# "over 45.5" is Over 45.5) and a team's handicap ("Browns -3.5"). It builds on `parse_text`
# for the market words and the name, and does not call a model.

_EXPLICIT = re.compile(r"\b(over|under|o|u)\s*(\d{1,3}(?:\.\d)?)(?![\w.])")
_PLUS_COUNT = re.compile(r"\b(\d{1,3})\s*\+(?!\s*\d)")
_AT_LEAST = re.compile(r"\b(?:at least|minimum|min)\s+(\d{1,3})\b|\b(\d{1,3})\s+or more\b")
_HANDICAP = re.compile(r"(?<![\w.])([+-])\s?(\d{1,2}(?:\.5)?)(?![\w.])")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3})(\.5)?(?![\w.])")
_UNDER_WORD = re.compile(r"\bunder\b")
_YARDS_WORD = re.compile(r"\b(yd|yds|yard|yards)\b")
_LIVE_FILLER = re.compile(r"\b(got|gets|get|has|have|had|hit|hits|each|under|least|more|or|"
                          r"him|odds|price|prices|now|live|right|currently|cover|min|minimum)\b")
HALF = Decimal("0.5")


@dataclass(frozen=True)
class LiveRead:
    parsed: Parsed
    who: str                    # who the text names, as typed: a surname, a full name or a team
    side: str                   # "Over" or "Under"
    line: Decimal | None        # the line the words ask for; None when they name none
    handicap: Decimal | None    # a team's spread: "Browns -3.5" is -3.5
    yards_of_any_kind: bool     # "yards" and nothing says receiving, rushing or passing


def read_live(text: str) -> LiveRead:
    raw = (text or "")[:MAX_TEXT].lower()
    parsed = parse_text(text)
    side = "Under" if _UNDER_WORD.search(raw) or re.search(r"\bu\s*\d", raw) else "Over"
    work = raw
    line: Decimal | None = None
    handicap: Decimal | None = None

    def take(match: re.Match[str]) -> None:
        nonlocal work
        work = work[:match.start()] + " " * (match.end() - match.start()) + work[match.end():]

    if m := _EXPLICIT.search(work):
        line = Decimal(m.group(2))
        side = "Under" if m.group(1).startswith("u") else "Over"
        take(m)
    elif m := _PLUS_COUNT.search(work):
        line = Decimal(m.group(1)) - HALF  # "5+" is 5 or more: Over 4.5
        take(m)
    elif m := _AT_LEAST.search(work):
        line = Decimal(m.group(1) or m.group(2)) - HALF
        take(m)
    if line is None and (m := _HANDICAP.search(work)):
        handicap = Decimal(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        take(m)
    if line is None and handicap is None and (m := _NUMBER.search(work)):
        # "4 catches" is four or more (Over 3.5); a bare ".5" is the line as written.
        line = Decimal(m.group(1)) + HALF if m.group(2) else Decimal(m.group(1)) - HALF
        take(m)

    market_key = parsed.market_key
    if handicap is not None and market_key is None:
        market_key = "spreads"
    # The name is read from the text with the numbers taken out ("u3.5" would otherwise stay
    # in it); the market and the play come from the whole line.
    who = _LIVE_FILLER.sub(" ", parse_text(work).name_text)
    who = re.sub(r"\s+", " ", who).strip()
    kind_known = market_key is not None or parsed.play is not None
    return LiveRead(
        parsed=replace(parsed, market_key=market_key), who=who, side=side, line=line,
        handicap=handicap,
        yards_of_any_kind=bool(_YARDS_WORD.search(parsed.text)) and not kind_known)
