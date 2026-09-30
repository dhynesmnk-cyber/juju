# Ported from parlaytracker@c3bd43c parlaytracker/ingest/nflverse.py. Changes: nflverse's
# release CSVs are read over Juju's HTTP client instead of through nflreadpy and polars (the
# worker has 512 MB, and only a few columns are needed); Juju's `Stat`s replace parlaytracker's
# market types; there are no snap counts (Juju never settles a missing player to zero); and
# `game_stats` returns every stat line of one game, for checking a whole box score at once.
"""nflverse: the next-day check of the stats Juju settles on (docs/GOALS.md sections 5 and 10).

Used only by the worker. Players map through the ID columns only (ESPN id -> gsis_id), never by
name. Each dataset is loaded at most once per `NflverseData` (one per job run), and every load
goes through the `nflverse` circuit breaker.

`STAT_COLUMNS` holds only the stats whose nflverse columns gave exactly ESPN's value for every
player in the recorded PHI @ CHI game (tests/fixtures/nflverse). Two needed care: ESPN's solo
tackles are nflverse's `def_tackles_solo + def_tackles_with_assist`, and its total tackles add
`def_tackle_assists`. The longest rush and reception aren't in the weekly stats, so they are not
checked here.
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

# The columns kept from each dataset. A missing one is a format change (`schema`).
COLUMNS: dict[str, tuple[str, ...]] = {
    "schedules": ("game_id", "espn", "home_score", "away_score"),
    "players": ("gsis_id", "espn_id"),
    "player_stats": ("player_id", "game_id",
                     *sorted({c for cols in STAT_COLUMNS.values() for c, _ in cols})),
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


def read_csv(raw: bytes, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """The rows of a CSV file (gzipped or not), keeping only `columns`. Raises KeyError naming
    the columns that are missing."""
    stream = gzip.GzipFile(fileobj=io.BytesIO(raw)) if raw[:2] == b"\x1f\x8b" \
        else io.BytesIO(raw)
    reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8", newline=""))
    missing = set(columns) - set(reader.fieldnames or ())
    if missing:
        raise KeyError(sorted(missing))
    return [{c: row[c] for c in columns} for row in reader]


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
        self._indexes: dict[tuple[str, int, str], dict] = {}

    # --- loading -----------------------------------------------------------------------------

    def load(self, dataset: str, season: int) -> list[dict[str, str]]:
        key = (dataset, 0 if "{season}" not in FILES[dataset] else season)
        if key in self._rows:
            return self._rows[key]
        self._breakers.allow(SOURCE)  # raises ProviderOpen
        try:
            raw = self._loader(dataset, season)
        except FetchError as e:
            self._fail(dataset, e.kind, e.detail)
        except Exception as e:
            self._fail(dataset, FailureKind.TRANSIENT, f"{type(e).__name__}: {e}"[:300])
        try:
            rows = read_csv(raw, COLUMNS[dataset])
            for row in rows:  # every number must read as one, or none of the file is used
                if dataset == "player_stats":
                    stat_values(row)
                elif dataset == "schedules":
                    _score(row["home_score"]), _score(row["away_score"])
        except KeyError as e:
            self._fail(dataset, FailureKind.SCHEMA, f"missing columns {e.args[0]}")
        except ValueError as e:
            self._fail(dataset, FailureKind.SCHEMA, str(e)[:300])
        except (OSError, EOFError, UnicodeDecodeError, csv.Error) as e:  # truncated or corrupt
            self._fail(dataset, FailureKind.TRANSIENT, f"{type(e).__name__}: {e}"[:300])
        self._breakers.success(SOURCE)
        self._rows[key] = rows
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
