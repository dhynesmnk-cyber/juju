# Ported from parlaytracker@c3bd43c parlaytracker/ingest/nflverse.py. Changes: nflverse's
# release CSVs are read over Juju's HTTP client instead of through nflreadpy and polars (the
# worker has 512 MB, and only a few columns are needed); Juju's `Stat`s replace parlaytracker's
# market types; there are no snap counts (Juju never settles a missing player to zero);
# `game_stats` returns every stat line of one game, for checking a whole box score at once; and
# the play-by-play is new (`plays`, `play_value`), for the play that decided a bet.
"""nflverse: the next-day check of the stats Juju settles on (docs/GOALS.md sections 5 and 10).

Used only by the worker. Players map through the ID columns only (ESPN id -> gsis_id), never by
name. Each dataset is loaded at most once per `NflverseData` (one per job run), and every load
goes through the `nflverse` circuit breaker.

`STAT_COLUMNS` holds only the stats whose nflverse columns gave exactly ESPN's value for every
player in the recorded PHI @ CHI game (tests/fixtures/nflverse). Two needed care: ESPN's solo
tackles are nflverse's `def_tackles_solo + def_tackles_with_assist`, and its total tackles add
`def_tackle_assists`. The longest rush and reception aren't in the weekly stats, so they are not
checked against the weekly stats; the play-by-play has them (`PLAY_STATS`), and adds up to the
same totals, which is what lets `worker/deciding.py` name the exact play that decided a bet.
"""
import csv
import gzip
import io
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import NoReturn
from urllib.parse import urlsplit

import httpx

from juju.core.enums import FailureKind, Stat
from juju.ingest.http import FetchError, RateLimiter, client
from juju.ingest.router import Breakers

log = logging.getLogger("juju.nflverse")

SOURCE = "nflverse"
BASE_URL = "https://github.com/nflverse/nflverse-data/releases/download"
FILES = {
    "schedules": "schedules/games.csv.gz",
    "players": "players/players.csv.gz",
    "player_stats": "stats_player/stats_player_week_{season}.csv.gz",
    "pbp": "pbp/play_by_play_{season}.csv.gz",
}
MAX_BYTES = 60_000_000  # a full season's play-by-play is about 30 MB gzipped
LIMITER = RateLimiter(per_host_interval=1.0, per_minute=30)

# The columns summed for each stat, with a weight (a field goal is worth 3 kicking points).
STAT_COLUMNS: dict[Stat, tuple[tuple[str, int], ...]] = {
    Stat.RECEPTIONS: (("receptions", 1),),
    Stat.RECEIVING_YARDS: (("receiving_yards", 1),),
    Stat.RECEIVING_TDS: (("receiving_tds", 1),),
    Stat.RUSHING_YARDS: (("rushing_yards", 1),),
    Stat.RUSH_ATTEMPTS: (("carries", 1),),
    Stat.RUSH_TDS: (("rushing_tds", 1),),
    Stat.PASSING_YARDS: (("passing_yards", 1),),
    Stat.PASS_COMPLETIONS: (("completions", 1),),
    Stat.PASS_ATTEMPTS: (("attempts", 1),),
    Stat.PASS_TDS: (("passing_tds", 1),),
    Stat.INTERCEPTIONS_THROWN: (("passing_interceptions", 1),),
    # ESPN's touchdowns: rushing, receiving, returns and defence (never passing).
    Stat.TOUCHDOWNS: (("rushing_tds", 1), ("receiving_tds", 1), ("special_teams_tds", 1),
                      ("def_tds", 1)),
    Stat.FIELD_GOALS: (("fg_made", 1),),
    Stat.KICKING_POINTS: (("fg_made", 3), ("pat_made", 1)),
    Stat.SACKS: (("def_sacks", 1),),
    Stat.TACKLES_ASSISTS: (("def_tackles_solo", 1), ("def_tackles_with_assist", 1),
                           ("def_tackle_assists", 1)),
    Stat.SOLO_TACKLES: (("def_tackles_solo", 1), ("def_tackles_with_assist", 1)),
    Stat.DEF_INTERCEPTIONS: (("def_interceptions", 1),),
}
CHECKED_STATS = frozenset(STAT_COLUMNS)

# Play-by-play: what each play added to a player's stat, for finding the play that decided a
# bet. Checked on the recorded game: summed (or, for the longest, maxed) over the plays, these
# give nflverse's own totals. Tackles aren't in the play-by-play in a usable form.
_PLAY_NUMBERS = ("play_id", "qtr", "rushing_yards", "receiving_yards", "passing_yards")
_PLAY_FLAGS = ("two_point_attempt", "rush_attempt", "complete_pass", "pass_attempt", "sack",
               "pass_touchdown", "rush_touchdown", "touchdown", "interception")
_PLAY_PLAYERS = ("rusher_player_id", "receiver_player_id", "passer_player_id", "td_player_id",
                 "interception_player_id", "kicker_player_id", "sack_player_id",
                 "half_sack_1_player_id", "half_sack_2_player_id")
MAX_STATS = frozenset({Stat.LONGEST_RUSH, Stat.LONGEST_RECEPTION})
PLAY_STATS = frozenset({
    Stat.RUSHING_YARDS, Stat.RUSH_ATTEMPTS, Stat.RUSH_TDS, Stat.LONGEST_RUSH,
    Stat.RECEPTIONS, Stat.RECEIVING_YARDS, Stat.RECEIVING_TDS, Stat.LONGEST_RECEPTION,
    Stat.PASSING_YARDS, Stat.PASS_COMPLETIONS, Stat.PASS_ATTEMPTS, Stat.PASS_TDS,
    Stat.INTERCEPTIONS_THROWN, Stat.TOUCHDOWNS, Stat.FIELD_GOALS, Stat.KICKING_POINTS,
    Stat.SACKS, Stat.DEF_INTERCEPTIONS,
})

# The columns kept from each dataset. A missing one is a format change (`schema`).
COLUMNS: dict[str, tuple[str, ...]] = {
    "schedules": ("game_id", "espn", "home_score", "away_score"),
    "players": ("gsis_id", "espn_id"),
    "player_stats": ("player_id", "game_id",
                     *sorted({c for cols in STAT_COLUMNS.values() for c, _ in cols})),
    "pbp": ("game_id", "time", "desc", "field_goal_result", "extra_point_result",
            *_PLAY_NUMBERS, *_PLAY_FLAGS, *_PLAY_PLAYERS),
}

Loader = Callable[[str, int], bytes]


class NflverseError(Exception):
    """A dataset couldn't be loaded, or isn't shaped as expected."""

    def __init__(self, dataset: str, kind: FailureKind, detail: str):
        super().__init__(f"nflverse {dataset}: {detail}")
        self.dataset = dataset
        self.kind = kind


def nfl_season(start_time: datetime) -> int:
    """The season a game belongs to: September to February all count as the starting year."""
    return start_time.year if start_time.month >= 3 else start_time.year - 1


def url_for(dataset: str, season: int) -> str:
    return f"{BASE_URL}/{FILES[dataset].format(season=season)}"


def default_loader(dataset: str, season: int) -> bytes:
    """Download one release file (GitHub redirects to its storage host). Raises FetchError."""
    url = url_for(dataset, season)
    LIMITER.acquire(urlsplit(url).netloc, max_wait=10.0)
    try:
        with client().stream("GET", url, follow_redirects=True,
                             headers={"Accept": "*/*"}) as response:
            status = response.status_code
            if status == 403:
                raise FetchError(url, FailureKind.BLOCKED, "403 forbidden", status)
            if status == 429:
                raise FetchError(url, FailureKind.THROTTLED, "HTTP 429", status)
            if status >= 400:
                raise FetchError(url, FailureKind.TRANSIENT, f"HTTP {status}", status)
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise FetchError(url, FailureKind.IMPLAUSIBLE,
                                     f"larger than {MAX_BYTES} bytes", status)
            return bytes(body)
    except httpx.TimeoutException as e:
        raise FetchError(url, FailureKind.TRANSIENT, f"timeout: {type(e).__name__}") from e
    except httpx.TransportError as e:
        raise FetchError(url, FailureKind.TRANSIENT,
                         f"connection error: {type(e).__name__}") from e


def read_csv(raw: bytes, columns: tuple[str, ...],
             keep: Callable[[dict[str, str]], bool] | None = None) -> list[dict[str, str]]:
    """The rows of a CSV file (gzipped or not) that `keep` accepts, with only `columns`. The file
    is read as a stream, so a season's play-by-play never sits in memory whole. Raises KeyError
    naming the columns that are missing."""
    stream = gzip.GzipFile(fileobj=io.BytesIO(raw)) if raw[:2] == b"\x1f\x8b" \
        else io.BytesIO(raw)
    reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8", newline=""))
    missing = set(columns) - set(reader.fieldnames or ())
    if missing:
        raise KeyError(sorted(missing))
    return [{c: row[c] for c in columns} for row in reader if keep is None or keep(row)]


def _number(text: str) -> Decimal:
    """nflverse leaves a zero stat empty (or "NA") on a player's line: that is a real 0."""
    if text in ("", "NA"):
        return Decimal(0)
    try:
        value = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"not a number: {text!r}") from e
    if not value.is_finite():
        raise ValueError(f"not a number: {text!r}")
    return value


def _score(text: str) -> int | None:
    """A final score ("27", or "27.0"); None while the game has none ("" or "NA")."""
    if text in ("", "NA"):
        return None
    value = _number(text)
    if value != value.to_integral_value() or not 0 <= value < 200:
        raise ValueError(f"not a score: {text!r}")
    return int(value)


def _is(row: dict[str, str], flag: str) -> bool:
    return row[flag] in ("1", "1.0")


def play_value(row: dict[str, str], gsis_id: str, stat: Stat) -> Decimal | None:
    """What one play added to a player's stat (a yardage, a count or, for the longest, the
    play's length), or None if the play doesn't count for him. Two-point tries never count."""
    if _is(row, "two_point_attempt"):
        return None
    one = Decimal(1)
    rusher = _is(row, "rush_attempt") and row["rusher_player_id"] == gsis_id
    caught = _is(row, "complete_pass") and row["receiver_player_id"] == gsis_id
    threw = (_is(row, "pass_attempt") and not _is(row, "sack")
             and row["passer_player_id"] == gsis_id)
    scored = _is(row, "touchdown") and row["td_player_id"] == gsis_id
    kicker = row["kicker_player_id"] == gsis_id
    match stat:
        case Stat.RUSHING_YARDS | Stat.LONGEST_RUSH if rusher:
            return _number(row["rushing_yards"])
        case Stat.RUSH_ATTEMPTS if rusher:
            return one
        case Stat.RUSH_TDS if scored and _is(row, "rush_touchdown"):
            return one
        case Stat.RECEIVING_YARDS | Stat.LONGEST_RECEPTION if caught:
            return _number(row["receiving_yards"])
        case Stat.RECEPTIONS if caught:
            return one
        case Stat.RECEIVING_TDS if scored and _is(row, "pass_touchdown"):
            return one
        case Stat.PASSING_YARDS if threw and _is(row, "complete_pass"):
            return _number(row["passing_yards"])
        case Stat.PASS_COMPLETIONS if threw and _is(row, "complete_pass"):
            return one
        case Stat.PASS_ATTEMPTS if threw:
            return one
        case Stat.PASS_TDS if threw and _is(row, "pass_touchdown"):
            return one
        case Stat.INTERCEPTIONS_THROWN if threw and _is(row, "interception"):
            return one
        case Stat.TOUCHDOWNS if scored:
            return one
        case Stat.FIELD_GOALS if kicker and row["field_goal_result"] == "made":
            return one
        case Stat.KICKING_POINTS if kicker and row["field_goal_result"] == "made":
            return Decimal(3)
        case Stat.KICKING_POINTS if kicker and row["extra_point_result"] == "good":
            return one
        case Stat.SACKS if row["sack_player_id"] == gsis_id:
            return one
        case Stat.SACKS if gsis_id in (row["half_sack_1_player_id"],
                                       row["half_sack_2_player_id"]):
            return Decimal("0.5")
        case Stat.DEF_INTERCEPTIONS if (_is(row, "interception")
                                        and row["interception_player_id"] == gsis_id):
            return one
    return None


def stat_values(row: dict[str, str]) -> dict[Stat, Decimal]:
    """Every checked stat on one player's line. Having a line means he played, so an empty
    column is 0 (the same rule as ESPN's box score)."""
    return {stat: sum((_number(row[c]) * w for c, w in cols), Decimal(0))
            for stat, cols in STAT_COLUMNS.items()}


class NflverseData:
    """Lookups over the datasets, loaded lazily and cached for the life of the object."""

    def __init__(self, breakers: Breakers, loader: Loader = default_loader):
        self._breakers = breakers
        self._loader = loader
        self._rows: dict[tuple[str, int], list[dict[str, str]]] = {}
        self._plays: dict[tuple[int, frozenset[str]], dict[str, list[dict[str, str]]]] = {}
        self._indexes: dict[tuple[str, int, str], dict] = {}

    # --- loading -----------------------------------------------------------------------------

    def load(self, dataset: str, season: int) -> list[dict[str, str]]:
        key = (dataset, 0 if "{season}" not in FILES[dataset] else season)
        if key not in self._rows:
            self._rows[key] = self._fetch(dataset, season)
        return self._rows[key]

    def plays(self, season: int, game_ids: frozenset[str]) -> dict[str, list[dict[str, str]]]:
        """The play-by-play of these nflverse games, each in order. Other games' plays are
        dropped while the file is read; one download per season per run."""
        key = (season, game_ids)
        if key not in self._plays:
            by_game: dict[str, list[dict[str, str]]] = {g: [] for g in game_ids}
            for row in self._fetch("pbp", season, lambda r: r["game_id"] in game_ids):
                by_game[row["game_id"]].append(row)
            for rows in by_game.values():
                rows.sort(key=lambda r: _number(r["play_id"]))
            self._plays[key] = by_game
        return self._plays[key]

    def _fetch(self, dataset: str, season: int,
               keep: Callable[[dict[str, str]], bool] | None = None) -> list[dict[str, str]]:
        self._breakers.allow(SOURCE)  # raises ProviderOpen
        try:
            raw = self._loader(dataset, season)
        except FetchError as e:
            self._fail(dataset, e.kind, e.detail)
        except Exception as e:
            self._fail(dataset, FailureKind.TRANSIENT, f"{type(e).__name__}: {e}"[:300])
        try:
            rows = read_csv(raw, COLUMNS[dataset], keep)
            for row in rows:  # every number must read as one, or none of the file is used
                if dataset == "player_stats":
                    stat_values(row)
                elif dataset == "schedules":
                    _score(row["home_score"]), _score(row["away_score"])
                elif dataset == "pbp":
                    for c in _PLAY_NUMBERS:
                        _number(row[c])
        except KeyError as e:
            self._fail(dataset, FailureKind.SCHEMA, f"missing columns {e.args[0]}")
        except ValueError as e:
            self._fail(dataset, FailureKind.SCHEMA, str(e)[:300])
        except (OSError, EOFError, UnicodeDecodeError, csv.Error) as e:  # truncated or corrupt
            self._fail(dataset, FailureKind.TRANSIENT, f"{type(e).__name__}: {e}"[:300])
        self._breakers.success(SOURCE)
        return rows

    def _fail(self, dataset: str, kind: FailureKind, detail: str) -> NoReturn:
        self._breakers.failure(SOURCE, kind, detail)
        raise NflverseError(dataset, kind, detail)

    def _index(self, dataset: str, season: int, column: str) -> dict[str, dict[str, str]]:
        cache_key = (dataset, season, column)
        if cache_key not in self._indexes:
            self._indexes[cache_key] = {
                row[column]: row for row in self.load(dataset, season) if row[column]}
        return self._indexes[cache_key]

    # --- lookups -----------------------------------------------------------------------------

    def _game(self, season: int, espn_event_id: str) -> dict[str, str] | None:
        return self._index("schedules", season, "espn").get(str(espn_event_id))

    def final_score(self, season: int, espn_event_id: str) -> tuple[int, int] | None:
        """(home, away) once nflverse has the game's final score; None until then."""
        game = self._game(season, espn_event_id)
        if game is None:
            return None
        home, away = _score(game["home_score"]), _score(game["away_score"])
        return None if home is None or away is None else (home, away)

    def game_id(self, season: int, espn_event_id: str) -> str | None:
        """nflverse's id for the game ("2026_03_PHI_CHI")."""
        game = self._game(season, espn_event_id)
        return game["game_id"] if game else None

    def game_stats(self, season: int, espn_event_id: str) -> dict[str, dict[str, str]]:
        """Every player's line in one game, by gsis_id. Empty until nflverse publishes it."""
        game = self._game(season, espn_event_id)
        if game is None:
            return {}
        return {row["player_id"]: row for row in self.load("player_stats", season)
                if row["game_id"] == game["game_id"]}

    def gsis_id(self, espn_athlete_id: str) -> str | None:
        player = self._index("players", 0, "espn_id").get(str(espn_athlete_id))
        return player["gsis_id"] or None if player else None

    def espn_id(self, gsis_id: str) -> str | None:
        player = self._index("players", 0, "gsis_id").get(gsis_id)
        return player["espn_id"] or None if player else None
