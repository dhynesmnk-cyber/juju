# Ported from parlaytracker@c3bd43c parlaytracker/ingest/espn.py. Changes: NFL stats are keyed by
# Juju's Stat (with longest-play and made/attempted columns), the web-app fetch helpers are gone
# (the API never calls ESPN), and `parse_plays` reads notable plays from the same summary.
"""ESPN client and parsers (SPEC.md section 6.1).

Nothing outside this module touches ESPN's raw JSON: parsers validate the parts they use
with Pydantic and return typed dataclasses. A malformed event is reported for that event
only; a malformed document raises SchemaError.
"""
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from juju.core.enums import DataSource, EventStatus, PlayKind, Sport, Stat
from juju.ingest.http import RateLimiter

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
BASE_PATH = "/apis/site/v2/sports"
SPORT_PATHS = {
    Sport.NFL: "football/nfl",
    Sport.NBA: "basketball/nba",
    Sport.MLB: "baseball/mlb",
    Sport.NHL: "hockey/nhl",
}
# Tried in order (section 6.1). cdn.espn.com serves the box score only, wrapped in
# `gamepackageJSON`; see CDN_URL and `parse_box_score`.
HOSTS: list[tuple[DataSource, str]] = [
    (DataSource.ESPN_WEB, "https://site.web.api.espn.com"),
    (DataSource.ESPN_SITE, "https://site.api.espn.com"),
]

CDN_URL = "https://cdn.espn.com/core/{league}/game"
CDN_LEAGUES = {Sport.NFL: "nfl", Sport.NBA: "nba", Sport.MLB: "mlb", Sport.NHL: "nhl"}

# One limiter per process for all ESPN hosts: 1 request / 2 s per host, 20 / minute overall.
LIMITER = RateLimiter(per_host_interval=2.0, per_minute=20)


class SchemaError(Exception):
    """ESPN's document doesn't have the shape the parser expects (section 8.3: `schema`)."""


def game_day(moment: datetime) -> date:
    """ESPN files games under their US Eastern calendar date (verified, section 3)."""
    return moment.astimezone(ET).date()


def today_game_day(now: datetime | None = None) -> date:
    return game_day(now or datetime.now(tz=ET))


# --- Parsed results ------------------------------------------------------------------------


@dataclass(frozen=True)
class Team:
    espn_id: str
    abbreviation: str
    name: str


@dataclass(frozen=True)
class Game:
    espn_event_id: str
    sport: Sport
    start_time: datetime  # UTC
    status: EventStatus
    status_detail: str
    period: int | None
    clock_seconds: int | None
    home: Team
    away: Team
    home_score: int | None
    away_score: int | None

    @property
    def label(self) -> str:
        return f"{self.away.abbreviation} @ {self.home.abbreviation}"


@dataclass(frozen=True)
class ScoreboardResult:
    games: list[Game]
    errors: dict[str, str]  # event id (or position) -> why it couldn't be parsed


@dataclass(frozen=True)
class RosterPlayer:
    espn_athlete_id: str
    name: str
    position: str
    group: str  # e.g. "offense", "injuredReserveOrOut"; "" when ESPN doesn't group
    unavailable: bool  # injured, suspended or otherwise not active

    @property
    def label(self) -> str:
        return f"{self.name} ({self.position})" if self.position else self.name


@dataclass(frozen=True)
class BoxScore:
    """What settlement needs from a summary (or cdn) document."""
    espn_event_id: str
    status: EventStatus
    home_espn_team_id: str
    away_espn_team_id: str
    home_score: int | None
    away_score: int | None
    # stat -> ESPN athlete id -> value so far
    stats: dict[Stat, dict[str, Decimal]]
    did_not_play: frozenset[str]  # athletes ESPN says didn't play (NBA `didNotPlay`)
    # Everyone listed anywhere in the box score: they have played, so a stat they lack is 0.
    appeared: frozenset[str] = frozenset()


# --- Raw shapes (only the fields we use) ---------------------------------------------------


class _TeamRaw(BaseModel):
    id: str
    abbreviation: str
    displayName: str


class _CompetitorRaw(BaseModel):
    homeAway: Literal["home", "away"]
    score: str | None = None
    team: _TeamRaw


class _CompetitionRaw(BaseModel):
    competitors: list[_CompetitorRaw]


class _StatusTypeRaw(BaseModel):
    name: str
    state: Literal["pre", "in", "post"]
    completed: bool = False
    detail: str = ""


class _StatusRaw(BaseModel):
    type: _StatusTypeRaw
    period: int | None = None
    clock: float | None = None


class _EventRaw(BaseModel):
    id: str
    date: datetime
    status: _StatusRaw
    competitions: list[_CompetitionRaw]


class _PositionRaw(BaseModel):
    abbreviation: str = ""


class _AthleteStatusRaw(BaseModel):
    type: str = ""
    name: str = ""


class _AthleteRaw(BaseModel):
    id: str
    fullName: str | None = None
    displayName: str | None = None
    position: _PositionRaw | None = None
    status: _AthleteStatusRaw | None = None


# --- Status mapping ------------------------------------------------------------------------

_BREAK_NAMES = {"STATUS_HALFTIME", "STATUS_END_PERIOD", "STATUS_END_OF_PERIOD"}
_warned_names: set[str] = set()


def map_status(name: str, state: str, completed: bool) -> EventStatus:
    """ESPN status -> EventStatus, by name first and state second (section 6.1).

    A postponed game has state "post" too, so "post" alone never means final.
    """
    if name == "STATUS_POSTPONED":
        return EventStatus.POSTPONED
    if name in ("STATUS_CANCELED", "STATUS_CANCELLED"):
        return EventStatus.CANCELLED
    if name == "STATUS_FINAL" or (state == "post" and completed):
        return EventStatus.FINAL
    if name in _BREAK_NAMES:
        return EventStatus.BREAK
    if "DELAY" in name or "SUSPENDED" in name:
        return EventStatus.DELAYED
    if name not in ("STATUS_SCHEDULED", "STATUS_IN_PROGRESS") and name not in _warned_names:
        _warned_names.add(name)
        log.warning("unrecognised ESPN status %s (state %s); mapping by state", name, state)
    if state == "in":
        return EventStatus.IN_PROGRESS
    if state == "pre":
        return EventStatus.SCHEDULED
    return EventStatus.POSTPONED  # post but not completed: not played to a finish


# --- Parsers -------------------------------------------------------------------------------


def _score(raw: str | None, state: str) -> int | None:
    if state == "pre" or raw is None or raw == "":
        return None
    return int(raw)


def _parse_event(sport: Sport, raw: Any) -> Game:
    event = _EventRaw.model_validate(raw)
    if len(event.competitions) != 1:
        raise ValueError(f"expected 1 competition, got {len(event.competitions)}")
    sides = {c.homeAway: c for c in event.competitions[0].competitors}
    if set(sides) != {"home", "away"}:
        raise ValueError("expected one home and one away competitor")
    st = event.status
    teams = {k: Team(v.team.id, v.team.abbreviation, v.team.displayName) for k, v in sides.items()}
    return Game(
        espn_event_id=event.id,
        sport=sport,
        start_time=event.date,
        status=map_status(st.type.name, st.type.state, st.type.completed),
        status_detail=st.type.detail,
        period=st.period,
        clock_seconds=None if st.clock is None else int(st.clock),
        home=teams["home"],
        away=teams["away"],
        home_score=_score(sides["home"].score, st.type.state),
        away_score=_score(sides["away"].score, st.type.state),
    )


def parse_scoreboard(sport: Sport, payload: Any) -> ScoreboardResult:
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise SchemaError("scoreboard has no 'events' list")
    games, errors = [], {}
    for n, raw in enumerate(payload["events"]):
        key = str(raw.get("id", f"#{n}")) if isinstance(raw, dict) else f"#{n}"
        try:
            games.append(_parse_event(sport, raw))
        except (ValidationError, ValueError) as e:
            errors[key] = str(e).splitlines()[0]
    games.sort(key=lambda g: (g.start_time, g.espn_event_id))
    return ScoreboardResult(games, errors)


def _roster_player(raw: Any, group: str) -> RosterPlayer:
    a = _AthleteRaw.model_validate(raw)
    name = a.fullName or a.displayName
    if not name:
        raise ValueError(f"athlete {a.id} has no name")
    status_type = (a.status.type if a.status else "").lower()
    unavailable = group in ("injuredReserveOrOut", "suspended") or status_type not in ("", "active")
    return RosterPlayer(a.id, name, a.position.abbreviation if a.position else "", group,
                        unavailable)


def parse_roster(payload: Any) -> list[RosterPlayer]:
    """NFL, NHL and MLB group athletes by position; NBA returns a flat list (verified)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("athletes"), list):
        raise SchemaError("roster has no 'athletes' list")
    players: dict[str, RosterPlayer] = {}
    try:
        for entry in payload["athletes"]:
            if isinstance(entry, dict) and "items" in entry:
                group = str(entry.get("position", ""))
                for raw in entry["items"]:
                    p = _roster_player(raw, group)
                    players.setdefault(p.espn_athlete_id, p)
            else:
                p = _roster_player(entry, "")
                players.setdefault(p.espn_athlete_id, p)
    except (ValidationError, ValueError, TypeError) as e:
        raise SchemaError(f"roster athlete: {str(e).splitlines()[0]}") from e
    return sorted(players.values(), key=lambda p: (p.unavailable, p.name))


# --- Box scores (summary and cdn) ------------------------------------------------------------

# Per stat: one or more sources, each (box-score groups, (column key, part, how)), added
# together for a player who appears in several. Keys, never positions or labels. A column
# holding "made/attempted" ("38/52") is read by `part` (0: made, 1: attempted). "max" columns
# (longest play) take the largest value rather than summing.
_NO_NAME = ""
Column = tuple[str, int, str]  # (key, part, "sum" | "max")
Source = tuple[tuple[str, ...], tuple[Column, ...]]


def _c(key: str, part: int = 0, how: str = "sum") -> Column:
    return key, part, how


STAT_COLUMNS: dict[Stat, tuple[Source, ...]] = {
    Stat.RECEPTIONS: ((("receiving",), (_c("receptions"),)),),
    Stat.RECEIVING_YARDS: ((("receiving",), (_c("receivingYards"),)),),
    Stat.RECEIVING_TDS: ((("receiving",), (_c("receivingTouchdowns"),)),),
    Stat.LONGEST_RECEPTION: ((("receiving",), (_c("longReception", how="max"),)),),
    Stat.RUSHING_YARDS: ((("rushing",), (_c("rushingYards"),)),),
    Stat.RUSH_ATTEMPTS: ((("rushing",), (_c("rushingAttempts"),)),),
    Stat.RUSH_TDS: ((("rushing",), (_c("rushingTouchdowns"),)),),
    Stat.LONGEST_RUSH: ((("rushing",), (_c("longRushing", how="max"),)),),
    Stat.PASSING_YARDS: ((("passing",), (_c("passingYards"),)),),
    Stat.PASS_COMPLETIONS: ((("passing",), (_c("completions/passingAttempts", 0),)),),
    Stat.PASS_ATTEMPTS: ((("passing",), (_c("completions/passingAttempts", 1),)),),
    Stat.PASS_TDS: ((("passing",), (_c("passingTouchdowns"),)),),
    Stat.INTERCEPTIONS_THROWN: ((("passing",), (_c("interceptions"),)),),
    # Any touchdown except a passing one (parlaytracker's rule): rushing, receiving, both
    # kinds of return and a defensive score. A player absent from all of these has none.
    Stat.TOUCHDOWNS: (
        (("rushing",), (_c("rushingTouchdowns"),)),
        (("receiving",), (_c("receivingTouchdowns"),)),
        (("kickReturns",), (_c("kickReturnTouchdowns"),)),
        (("puntReturns",), (_c("puntReturnTouchdowns"),)),
        (("defensive",), (_c("defensiveTouchdowns"),)),
    ),
    Stat.FIELD_GOALS: ((("kicking",), (_c("fieldGoalsMade/fieldGoalAttempts", 0),)),),
    Stat.KICKING_POINTS: ((("kicking",), (_c("totalKickingPoints"),)),),
    Stat.SACKS: ((("defensive",), (_c("sacks"),)),),
    Stat.TACKLES_ASSISTS: ((("defensive",), (_c("totalTackles"),)),),
    Stat.SOLO_TACKLES: ((("defensive",), (_c("soloTackles"),)),),
    Stat.DEF_INTERCEPTIONS: ((("interceptions",), (_c("interceptions"),)),),
}


class _BoxTeamIdRaw(BaseModel):
    id: str


class _BoxAthleteIdRaw(BaseModel):
    id: str


class _BoxCompetitorRaw(BaseModel):
    homeAway: Literal["home", "away"]
    score: str | None = None
    id: str | None = None
    team: _BoxTeamIdRaw | None = None


class _BoxCompetitionRaw(BaseModel):
    competitors: list[_BoxCompetitorRaw]
    status: _StatusRaw


class _BoxHeaderRaw(BaseModel):
    id: str
    competitions: list[_BoxCompetitionRaw]


class _BoxAthleteRaw(BaseModel):
    athlete: _BoxAthleteIdRaw
    stats: list[str] = []
    didNotPlay: bool = False


class _BoxGroupRaw(BaseModel):
    name: str | None = None
    keys: list[str] = []
    athletes: list[_BoxAthleteRaw] = []


class _BoxTeamPlayersRaw(BaseModel):
    statistics: list[_BoxGroupRaw] = []


class _BoxRaw(BaseModel):
    players: list[_BoxTeamPlayersRaw] = []


class _BoxDocRaw(BaseModel):
    header: _BoxHeaderRaw
    boxscore: _BoxRaw


def unwrap_cdn(payload: Any) -> Any:
    """cdn.espn.com wraps the summary document in `gamepackageJSON` (verified, section 6.1)."""
    if isinstance(payload, dict) and "gamepackageJSON" in payload:
        return payload["gamepackageJSON"]
    return payload


def _stat_value(text: str, part: int = 0) -> Decimal:
    if "/" in text:  # "38/52": made (part 0) or attempted (part 1)
        text = text.split("/")[part]
    try:
        value = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"not a number: {text!r}") from e
    if not value.is_finite():
        raise ValueError(f"not a number: {text!r}")
    return value


def _team_id(c: _BoxCompetitorRaw) -> str:
    team_id = c.team.id if c.team else c.id
    if team_id is None:
        raise ValueError("competitor has no team id")
    return team_id


def parse_box_score(payload: Any, sport: Sport = Sport.NFL) -> BoxScore:
    """Parse a summary or cdn document. Raises SchemaError if it isn't one we understand."""
    try:
        doc = _BoxDocRaw.model_validate(unwrap_cdn(payload))
        header = doc.header
        if len(header.competitions) != 1:
            raise ValueError(f"expected 1 competition, got {len(header.competitions)}")
        comp = header.competitions[0]
        sides = {c.homeAway: c for c in comp.competitors}
        if set(sides) != {"home", "away"}:
            raise ValueError("expected one home and one away competitor")
        state = comp.status.type.state
        stats: dict[Stat, dict[str, Decimal]] = {m: {} for m in STAT_COLUMNS}
        did_not_play: set[str] = set()
        appeared: set[str] = set()
        for team in doc.boxscore.players:
            for group in team.statistics:
                for a in group.athletes:
                    if a.didNotPlay:
                        did_not_play.add(a.athlete.id)
                    elif a.stats:
                        appeared.add(a.athlete.id)
                for stat, sources in STAT_COLUMNS.items():
                    for groups, columns in sources:
                        if (group.name or _NO_NAME) not in groups:
                            continue
                        idx = [(group.keys.index(key), part, how)  # ValueError if one is gone
                               for key, part, how in columns]
                        for a in group.athletes:
                            if a.didNotPlay or not a.stats:
                                continue
                            if len(a.stats) != len(group.keys):
                                raise ValueError(f"athlete {a.athlete.id} has {len(a.stats)}"
                                                 f" stats for {len(group.keys)} keys")
                            held = stats[stat].get(a.athlete.id)
                            for i, part, how in idx:
                                value = _stat_value(a.stats[i], part)
                                if held is None:
                                    held = value
                                elif how == "max":
                                    held = max(held, value)
                                else:
                                    held += value
                            stats[stat][a.athlete.id] = held
        return BoxScore(
            espn_event_id=header.id,
            status=map_status(comp.status.type.name, state, comp.status.type.completed),
            home_espn_team_id=_team_id(sides["home"]), away_espn_team_id=_team_id(sides["away"]),
            home_score=_score(sides["home"].score, state),
            away_score=_score(sides["away"].score, state),
            stats=stats, did_not_play=frozenset(did_not_play), appeared=frozenset(appeared),
        )
    except (ValidationError, ValueError, TypeError) as e:
        raise SchemaError(f"box score: {str(e).splitlines()[0]}") from e


# --- Notable plays (summary document) --------------------------------------------------------

# A non-scoring play this long gets a "Live now" chip.
BIG_PLAY_YARDS = 20
_YD = re.compile(r"^(?P<name>.+?)\s+(?P<yards>-?\d+)\s+Yd\b", re.IGNORECASE)
_PASS_FROM = re.compile(r"\bpass from (?P<passer>.+?)(?:\s*\(|$)", re.IGNORECASE)
_LEAD = re.compile(r"^(?:\([^)]*\)\s*)+")  # "(Shotgun) ", "(No Huddle, Shotgun) "
_ABBR = r"[A-Z][A-Za-z'\-]*\.\s?[A-Z][A-Za-z'\-]+(?:\s(?:Jr\.|Sr\.|II|III|IV))?"
_RUNNER = re.compile(rf"^(?P<name>{_ABBR})\s")
_RECEIVER = re.compile(rf"\bpass\b[^.]*?\bto (?P<name>{_ABBR})")
_PASSER = re.compile(rf"^(?P<name>{_ABBR}) pass\b")


@dataclass(frozen=True)
class RawPlay:
    """A notable play as ESPN describes it. Names are as printed: full names for scoring plays
    ("Jalen Hurts"), abbreviated otherwise ("J.Hurts"); the worker matches them to a roster."""
    espn_play_id: str
    sequence: int
    period: int | None
    clock: str | None
    wallclock: datetime | None
    kind: PlayKind
    yards: int | None
    text: str
    team_espn_id: str | None
    player_name: str | None
    passer_name: str | None  # the thrower of a touchdown pass or a long completion
    scoring: bool


class _PlayTypeRaw(BaseModel):
    text: str = ""


class _PlayPeriodRaw(BaseModel):
    number: int | None = None


class _PlayClockRaw(BaseModel):
    displayValue: str | None = None


class _PlayRaw(BaseModel):
    id: str
    sequenceNumber: str | None = None
    type: _PlayTypeRaw = _PlayTypeRaw()
    text: str = ""
    period: _PlayPeriodRaw = _PlayPeriodRaw()
    clock: _PlayClockRaw = _PlayClockRaw()
    wallclock: datetime | None = None
    statYardage: int | None = None
    scoringPlay: bool = False


class _DriveTeamRaw(BaseModel):
    id: str | None = None


class _DriveRaw(BaseModel):
    team: _DriveTeamRaw | None = None
    plays: list[_PlayRaw] = []


class _DrivesRaw(BaseModel):
    previous: list[_DriveRaw] = []
    current: _DriveRaw | None = None


class _ScoringTeamRaw(BaseModel):
    id: str | None = None


class _ScoringPlayRaw(BaseModel):
    id: str
    type: _PlayTypeRaw = _PlayTypeRaw()
    text: str = ""
    team: _ScoringTeamRaw | None = None


def _kind(type_text: str) -> PlayKind | None:
    t = type_text.lower()
    if "field goal good" in t:
        return PlayKind.FIELD_GOAL
    if "touchdown" in t:
        return PlayKind.TOUCHDOWN
    if t in ("rush",):
        return PlayKind.RUN
    if t in ("pass reception", "pass completion"):
        return PlayKind.CATCH
    return None


def parse_plays(payload: Any) -> list[RawPlay]:
    """Scoring plays and gains of BIG_PLAY_YARDS or more, oldest first. Anything we can't
    read is skipped (a chip is a convenience; the box score is what cards are built from)."""
    doc = unwrap_cdn(payload)
    if not isinstance(doc, dict):
        raise SchemaError("summary is not an object")
    try:
        drives = _DrivesRaw.model_validate(doc.get("drives") or {})
        scoring = {s.id: s for s in (_ScoringPlayRaw.model_validate(x)
                                     for x in doc.get("scoringPlays") or [])}
    except ValidationError as e:
        raise SchemaError(f"plays: {str(e).splitlines()[0]}") from e
    all_drives = list(drives.previous) + ([drives.current] if drives.current else [])
    out: dict[str, RawPlay] = {}
    for drive in all_drives:
        team_id = drive.team.id if drive.team else None
        for p in drive.plays:
            kind = _kind(p.type.text)
            yards = p.statYardage
            if kind is None or (not p.scoringPlay and (yards is None or yards < BIG_PLAY_YARDS)):
                continue
            text = _LEAD.sub("", p.text).strip()
            player = passer = None
            if p.scoringPlay and p.id in scoring:
                s = scoring[p.id]
                if m := _YD.match(s.text):
                    player = m.group("name").strip()
                    yards = int(m.group("yards"))
                if m := _PASS_FROM.search(s.text):
                    passer = m.group("passer").strip()
                if s.team and s.team.id:
                    team_id = s.team.id
            else:
                if kind is PlayKind.RUN and (m := _RUNNER.match(text)):
                    player = m.group("name")
                elif kind is PlayKind.CATCH and (m := _RECEIVER.search(text)):
                    player = m.group("name")
                    if pm := _PASSER.match(text):
                        passer = pm.group("name")
            try:
                sequence = int(p.sequenceNumber or 0)
            except ValueError:
                sequence = 0
            out[p.id] = RawPlay(p.id, sequence, p.period.number, p.clock.displayValue,
                                p.wallclock, kind, yards, text, team_id, player, passer,
                                p.scoringPlay)
    return sorted(out.values(), key=lambda r: (r.period or 0, r.sequence))
