# Ported from parlaytracker@c3bd43c tests/unit/test_nflverse.py. Changes: the tracker's lookups
# on Juju's streaming client (`NflverseLegs`) over real CSV files recorded today, so a case is
# staged by editing rows instead of polars frames; the game with no scores yet is NYJ @ DET with
# its scores blanked (the recorded PHI @ CHI is final now); and the snap counts load a fifth
# dataset, `player_ids`.
"""The nflverse loaders and ID mapping against a recorded slice (SPEC.md section 6.4)."""
from decimal import Decimal as D

import pytest

from juju.core.enums import FailureKind, HealthState
from juju.ingest.nflverse import NflverseError, nfl_season
from juju.ingest.router import Breakers, ProviderOpen
from juju.tracker.models import MarketType as M
from juju.tracker.nflverse import NflverseLegs
from tests.tracker_support import (
    ARI_SF,
    BRISSETT,
    JAYDEN_WILLIAMS,
    KICKOFF,
    MCBRIDE,
    SEUMALO,
    nflverse_loader,
)

SEASON = 2026
NYJ_DET = "401872954"


def _unplayed(tables):
    """NYJ @ DET as it was before kickoff: in the schedule, no scores yet."""
    for game in tables["schedules"].rows:
        if game["espn"] == NYJ_DET:
            game["home_score"] = game["away_score"] = ""


@pytest.fixture
def breakers() -> Breakers:
    return Breakers(engine=None)


@pytest.fixture
def data(breakers) -> NflverseLegs:
    return NflverseLegs(breakers, nflverse_loader())


def test_season_of_a_game():
    from datetime import UTC, datetime
    assert nfl_season(KICKOFF) == 2026
    assert nfl_season(datetime(2027, 1, 10, tzinfo=UTC)) == 2026  # a January playoff game
    assert nfl_season(datetime(2027, 2, 14, tzinfo=UTC)) == 2026


def test_final_score_by_espn_event_id(data):
    assert data.final_score(SEASON, ARI_SF) == (36, 30)  # home, away


def test_no_final_score_until_nflverse_has_one(breakers):
    data = NflverseLegs(breakers, nflverse_loader(_unplayed))
    assert data.final_score(SEASON, NYJ_DET) is None
    assert data.final_score(SEASON, ARI_SF) == (36, 30)
    assert data.final_score(SEASON, "not-a-game") is None


@pytest.mark.parametrize(("market", "expected"), [
    (M.PLAYER_RECEPTIONS, D("9")),
    (M.PLAYER_RECEIVING_YARDS, D("75")),
    (M.PLAYER_RUSHING_YARDS, D("0")),
    (M.PLAYER_PASSING_YARDS, D("0")),
])
def test_player_stats_map_through_the_id_columns(data, market, expected):
    assert data.stat_value(SEASON, ARI_SF, MCBRIDE, market) == expected


def test_a_player_with_a_row_but_no_receptions_is_a_real_zero(data):
    assert data.stat_value(SEASON, ARI_SF, BRISSETT, M.PLAYER_RECEPTIONS) == 0
    assert data.stat_value(SEASON, ARI_SF, BRISSETT, M.PLAYER_PASSING_YARDS) == 280


def test_a_player_with_no_stat_row_has_no_value(data):
    assert data.stat_value(SEASON, ARI_SF, SEUMALO, M.PLAYER_RECEPTIONS) is None


def test_an_unmapped_player_is_never_matched_by_name(data):
    assert not data.player_mapped("999")
    assert data.stat_value(SEASON, ARI_SF, "999", M.PLAYER_RECEPTIONS) is None
    assert data.offense_snaps(SEASON, ARI_SF, "999") is None


def test_offense_snaps_through_the_pfr_id(data):
    assert data.offense_snaps(SEASON, ARI_SF, SEUMALO) == 87.0
    assert data.offense_snaps(SEASON, ARI_SF, JAYDEN_WILLIAMS) == 4.0
    assert data.offense_snaps(SEASON, "not-a-game", SEUMALO) is None


def test_each_dataset_loads_once_however_many_lookups(breakers):
    calls: list[str] = []
    inner = nflverse_loader()

    def counting(dataset, season):
        calls.append(dataset)
        return inner(dataset, season)

    data = NflverseLegs(breakers, counting)
    for _ in range(3):
        data.final_score(SEASON, ARI_SF)
        data.stat_value(SEASON, ARI_SF, MCBRIDE, M.PLAYER_RECEPTIONS)
        data.offense_snaps(SEASON, ARI_SF, SEUMALO)
    assert sorted(calls) == ["player_ids", "player_stats", "players", "schedules",
                             "snap_counts"]


# --- Failures reach the breaker -------------------------------------------------------------


def test_a_download_failure_is_transient_and_recorded(breakers):
    def broken(dataset, season):
        raise ConnectionError("github is down")

    data = NflverseLegs(breakers, broken)
    with pytest.raises(NflverseError) as info:
        data.final_score(SEASON, ARI_SF)
    assert info.value.kind is FailureKind.TRANSIENT
    assert breakers["nflverse"].consecutive_failures == 1


def test_a_missing_column_is_a_schema_failure_that_opens_the_breaker(breakers):
    def renamed(tables):
        schedules = tables["schedules"]
        schedules.fields = ["espn_game" if f == "espn" else f for f in schedules.fields]
        for game in schedules.rows:
            game["espn_game"] = game.pop("espn")

    data = NflverseLegs(breakers, nflverse_loader(renamed))
    with pytest.raises(NflverseError) as info:
        data.final_score(SEASON, ARI_SF)
    assert info.value.kind is FailureKind.SCHEMA and "espn" in str(info.value)
    assert breakers["nflverse"].state is HealthState.OPEN
    with pytest.raises(ProviderOpen):  # the open breaker stops the next attempt
        NflverseLegs(breakers, nflverse_loader()).final_score(SEASON, ARI_SF)


def test_warm_loads_everything_and_reports_a_healthy_provider(data, breakers):
    data.warm(SEASON)
    assert breakers["nflverse"].state is HealthState.OK
    assert breakers["nflverse"].last_success_at is not None


def _set(player: str, columns: dict):
    """A patch that sets columns on one player's stat row. None writes an empty column and NaN
    writes "NA", as nflverse's files leave them."""
    def text(v) -> str:
        return "" if v is None else "NA" if v != v else str(v)

    def patch(tables):
        for row in tables["player_stats"].rows:
            if row["player_display_name"] == player:
                row.update({c: text(v) for c, v in columns.items()})
    return patch


@pytest.mark.parametrize("empty", [None, float("nan")])
def test_empty_and_na_stats_count_as_missing(breakers, empty):
    data = NflverseLegs(breakers, nflverse_loader(_set("Trey McBride", {"receptions": empty})))
    assert data.stat_value(SEASON, ARI_SF, MCBRIDE, M.PLAYER_RECEPTIONS) is None


@pytest.mark.parametrize(("market", "column", "value"), [
    (M.PLAYER_PASS_COMPLETIONS, "completions", 38),
    (M.PLAYER_INTERCEPTIONS, "passing_interceptions", 2),
    (M.PLAYER_FIELD_GOALS, "fg_made", 3),
])
def test_the_new_single_column_markets_read_their_own_column(breakers, market, column, value):
    data = NflverseLegs(breakers, nflverse_loader(_set("Jacoby Brissett", {column: value})))
    assert data.stat_value(SEASON, ARI_SF, BRISSETT, market) == value


def test_touchdowns_add_rushing_receiving_returns_and_defence(breakers):
    patch = _set("Jacoby Brissett",
                 {"rushing_tds": 1, "receiving_tds": 2, "special_teams_tds": 1, "def_tds": 1})
    data = NflverseLegs(breakers, nflverse_loader(patch))
    assert data.stat_value(SEASON, ARI_SF, BRISSETT, M.PLAYER_TOUCHDOWNS) == 5


def test_a_touchdown_column_left_empty_counts_as_zero_but_all_empty_is_no_stat_line(breakers):
    partial = _set("Jacoby Brissett", {"rushing_tds": 1, "receiving_tds": float("nan"),
                                       "special_teams_tds": None, "def_tds": None})
    data = NflverseLegs(breakers, nflverse_loader(partial))
    assert data.stat_value(SEASON, ARI_SF, BRISSETT, M.PLAYER_TOUCHDOWNS) == 1
    empty = _set("Jacoby Brissett", {c: None for c in ("rushing_tds", "receiving_tds",
                                                       "special_teams_tds", "def_tds")})
    data = NflverseLegs(breakers, nflverse_loader(empty))
    assert data.stat_value(SEASON, ARI_SF, BRISSETT, M.PLAYER_TOUCHDOWNS) is None
