"""The nflverse loader against the recorded PHI @ CHI slice (tests/fixtures/nflverse), and the
column mapping that decides which stats the next-day check may correct."""
import gzip
from datetime import UTC, datetime
from decimal import Decimal as D

import httpx
import pytest
import respx

from juju.core.enums import FailureKind, Stat
from juju.ingest import espn, nflverse
from juju.ingest.http import FetchError, RateLimiter
from juju.ingest.nflverse import (
    CHECKED_STATS, NflverseData, NflverseError, default_loader, nfl_season, read_csv,
    stat_values, url_for,
)
from juju.ingest.router import Breakers, ProviderOpen
from tests.support import BARKLEY, FIXTURES, PHI_CHI_ESPN_ID, SMITH, load, nflverse_loader

SEASON = 2026
PIT_CLE = "401872964"  # in nflverse's schedule, not played yet
BAUN = "3917657"       # Zack Baun: ESPN's solo tackles include nflverse's `with_assist`
ELLIOTT = "3050478"    # Jake Elliott, PHI kicker
STATS_FILE = FIXTURES / "nflverse" / "stats_player_week_2026.csv"


@pytest.fixture
def breakers() -> Breakers:
    return Breakers(engine=None)


@pytest.fixture
def data(breakers) -> NflverseData:
    return NflverseData(breakers, nflverse_loader())


def test_season_of_a_game():
    assert nfl_season(datetime(2026, 9, 29, tzinfo=UTC)) == 2026
    assert nfl_season(datetime(2027, 1, 10, tzinfo=UTC)) == 2026  # a January playoff game
    assert nfl_season(datetime(2027, 2, 14, tzinfo=UTC)) == 2026


def test_final_score_by_espn_event_id(data):
    assert data.final_score(SEASON, PHI_CHI_ESPN_ID) == (27, 7)  # home, away


def test_no_final_score_until_nflverse_has_one(data):
    assert data.final_score(SEASON, PIT_CLE) is None
    assert data.final_score(SEASON, "not-a-game") is None
    assert data.game_stats(SEASON, "not-a-game") == {}


def test_players_map_through_the_id_columns_only(data):
    gsis = data.gsis_id(BARKLEY)
    assert gsis == "00-0034844" and data.espn_id(gsis) == BARKLEY
    assert data.gsis_id("999") is None and data.espn_id("00-0000000") is None


def test_game_stats_and_values(data):
    lines = data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    assert len(lines) == 65
    barkley = stat_values(lines[data.gsis_id(BARKLEY)])
    assert barkley[Stat.RUSHING_YARDS] == 82 and barkley[Stat.TOUCHDOWNS] == 0
    baun = stat_values(lines[data.gsis_id(BAUN)])
    assert (baun[Stat.SOLO_TACKLES], baun[Stat.TACKLES_ASSISTS]) == (5, 9)
    kicker = stat_values(lines[data.gsis_id(ELLIOTT)])
    assert kicker[Stat.KICKING_POINTS] == 3 * kicker[Stat.FIELD_GOALS] + D(1)  # one PAT


def test_every_checked_stat_matches_espn_for_every_player_in_the_recorded_game(data):
    """Why these stats and no others: on the real game, nflverse and ESPN agree exactly for
    all 61 players ESPN listed. A mapping that drifts would 'correct' good numbers."""
    box = espn.parse_box_score(load(FIXTURES / "espn" / "nfl_summary_401872963_full.json"))
    lines = data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    assert len(box.appeared) == 61
    for athlete in box.appeared:
        theirs = stat_values(lines[data.gsis_id(athlete)])
        for stat in CHECKED_STATS:
            assert theirs[stat] == box.stats[stat].get(athlete, D(0)), (athlete, stat)
    assert Stat.LONGEST_RUSH not in CHECKED_STATS  # not in the weekly stats
    assert Stat.LONGEST_RECEPTION not in CHECKED_STATS


def test_an_empty_column_on_a_line_is_a_real_zero(data):
    lines = data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    line = dict(lines[data.gsis_id(SMITH)], passing_yards="", completions="NA")
    values = stat_values(line)
    assert values[Stat.PASSING_YARDS] == 0 and values[Stat.PASS_COMPLETIONS] == 0


def test_each_dataset_is_loaded_once(breakers):
    calls = []
    data = NflverseData(breakers, nflverse_loader(calls=calls))
    data.final_score(SEASON, PHI_CHI_ESPN_ID)
    data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    data.gsis_id(BARKLEY)
    data.espn_id("00-0034844")
    assert calls == ["schedules", "player_stats", "players"]


def test_read_csv_takes_gzip_or_plain_and_keeps_only_the_columns_asked():
    raw = b"a,b,c\n1,2,3\n"
    assert read_csv(raw, ("a", "c")) == [{"a": "1", "c": "3"}]
    assert read_csv(gzip.compress(raw), ("b",)) == [{"b": "2"}]
    with pytest.raises(KeyError):
        read_csv(raw, ("a", "z"))


def test_a_missing_column_is_a_schema_failure(breakers):
    header, *rows = STATS_FILE.read_text().splitlines()
    cut = ",".join(c for c in header.split(",") if c != "carries")
    data = NflverseData(breakers, nflverse_loader(replace={"player_stats": cut.encode()}))
    with pytest.raises(NflverseError) as e:
        data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    assert e.value.kind is FailureKind.SCHEMA and "carries" in str(e.value)
    assert breakers["nflverse"].is_open  # schema failures open at once


def test_a_value_that_is_not_a_number_is_a_schema_failure(breakers):
    text = STATS_FILE.read_text()
    header = text.splitlines()[0].split(",")
    lines = text.splitlines()
    first = lines[1].split(",")
    first[header.index("rushing_yards")] = "lots"
    bad = "\n".join([lines[0], ",".join(first), *lines[2:]]).encode()
    data = NflverseData(breakers, nflverse_loader(replace={"player_stats": bad}))
    with pytest.raises(NflverseError) as e:
        data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    assert e.value.kind is FailureKind.SCHEMA


def test_a_failed_download_counts_against_the_breaker(breakers):
    def failing(dataset, season):
        raise FetchError("https://github.com/x", FailureKind.TRANSIENT, "timeout")
    data = NflverseData(breakers, failing)
    for _ in range(3):
        with pytest.raises(NflverseError):
            data.final_score(SEASON, PHI_CHI_ESPN_ID)
    with pytest.raises(ProviderOpen):  # three in a row: the breaker opens
        data.final_score(SEASON, PHI_CHI_ESPN_ID)


def test_a_truncated_gzip_is_transient(breakers):
    raw = gzip.compress(STATS_FILE.read_bytes())[:500]
    data = NflverseData(breakers, nflverse_loader(replace={"player_stats": raw}))
    with pytest.raises(NflverseError) as e:
        data.game_stats(SEASON, PHI_CHI_ESPN_ID)
    assert e.value.kind is FailureKind.TRANSIENT


# --- The real loader, with GitHub mocked --------------------------------------------------------


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(nflverse, "LIMITER", RateLimiter(per_host_interval=0, per_minute=1000))


@respx.mock
def test_default_loader_follows_github_to_its_storage_host(no_wait):
    body = gzip.compress(STATS_FILE.read_bytes())
    storage = "https://release-assets.githubusercontent.com/stats.csv.gz"
    respx.get(url_for("player_stats", SEASON)).mock(
        return_value=httpx.Response(302, headers={"Location": storage}))
    respx.get(storage).mock(return_value=httpx.Response(200, content=body))
    assert default_loader("player_stats", SEASON) == body
    assert url_for("player_stats", SEASON).endswith("stats_player_week_2026.csv.gz")
    assert url_for("players", SEASON).endswith("players/players.csv.gz")


@respx.mock
@pytest.mark.parametrize(("status", "kind"), [
    (403, FailureKind.BLOCKED), (429, FailureKind.THROTTLED), (404, FailureKind.TRANSIENT)])
def test_default_loader_classifies_failures(no_wait, status, kind):
    respx.get(url_for("schedules", SEASON)).mock(return_value=httpx.Response(status))
    with pytest.raises(FetchError) as e:
        default_loader("schedules", SEASON)
    assert e.value.kind is kind


@respx.mock
def test_default_loader_refuses_an_implausibly_large_file(no_wait, monkeypatch):
    monkeypatch.setattr(nflverse, "MAX_BYTES", 100)
    respx.get(url_for("schedules", SEASON)).mock(
        return_value=httpx.Response(200, content=b"x" * 1000))
    with pytest.raises(FetchError) as e:
        default_loader("schedules", SEASON)
    assert e.value.kind is FailureKind.IMPLAUSIBLE
