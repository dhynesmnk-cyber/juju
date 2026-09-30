"""Finding the play that decided a bet: the pure parts, and the real play-by-play of PHI @ CHI."""
from decimal import Decimal as D

import pytest

from juju.core.enums import Stat
from juju.ingest.nflverse import NflverseData, play_value
from juju.ingest.router import Breakers
from juju.worker.deciding import STAT_KINDS, _desc, _pbp_clock, crossed, deciding_index, total
from tests.support import BARKLEY, PHI_CHI_ESPN_ID, SMITH, nflverse_loader


def values(*xs):
    return [None if x is None else D(x) for x in xs]


def test_crossed_means_more_than_the_line():
    assert crossed(D(5), D(6), D("5.5"))
    assert not crossed(D(6), D(7), D("5.5"))   # already past it
    assert not crossed(D(4), D(5), D("5.5"))
    assert crossed(D(4), D(6), D(5))           # a whole line: 6 beats 5, 5 would push
    assert not crossed(D(4), D(5), D(5))
    assert crossed(None, D(3), D("2.5"))       # the longest play's first value


def test_the_last_crossing_decides_a_sum():
    # 60, then a 20-yard loss, then 30 more: it was the 30 that finally took it past 73.5.
    assert deciding_index(values(60, 20, None, -20, 30), Stat.RUSHING_YARDS, D("73.5")) == 4
    assert deciding_index(values(60, 20), Stat.RUSHING_YARDS, D("73.5")) == 1
    assert deciding_index(values(60, 20, -20), Stat.RUSHING_YARDS, D("73.5")) is None
    assert deciding_index(values(1, 1, 1), Stat.RECEPTIONS, D("2.5")) == 2


def test_the_first_long_enough_play_decides_a_longest():
    assert deciding_index(values(12, 31, 45), Stat.LONGEST_RUSH, D("29.5")) == 1
    assert deciding_index(values(-3, 12), Stat.LONGEST_RUSH, D("29.5")) is None


def test_total_sums_or_takes_the_longest():
    assert total(values(4, None, 7), Stat.RUSHING_YARDS) == 11
    assert total(values(4, None, 7), Stat.LONGEST_RUSH) == 7
    assert total(values(-3), Stat.LONGEST_RUSH) == -3
    assert total(values(None), Stat.LONGEST_RECEPTION) == 0  # no catch: ESPN shows 0


def test_nflverse_text_is_tidied():
    assert _pbp_clock("08:56") == "8:56" and _pbp_clock("00:01") == "0:01"
    assert _pbp_clock("") is None
    assert _desc("(8:56) (Shotgun) 11-C.Keenum pass short middle") == \
        "(Shotgun) 11-C.Keenum pass short middle"
    assert _desc("(Shotgun) no clock here") == "(Shotgun) no clock here"


def test_each_stat_is_moved_by_its_kinds_of_play():
    from juju.core.enums import PlayKind as P
    assert STAT_KINDS[Stat.RECEPTIONS] == {P.CATCH, P.TOUCHDOWN}
    assert STAT_KINDS[Stat.TOUCHDOWNS] == {P.TOUCHDOWN}
    assert P.RUN in STAT_KINDS[Stat.RUSHING_YARDS]


@pytest.fixture(scope="module")
def pbp():
    data = NflverseData(Breakers(engine=None), nflverse_loader())
    game_id = data.game_id(2026, PHI_CHI_ESPN_ID)
    return data, data.plays(2026, frozenset({game_id}))[game_id]


@pytest.mark.parametrize(("athlete", "stat", "line", "final", "when", "starts"), [
    # His sixth catch, a 4-yarder late in the third, not his 30-yard one before half-time.
    (SMITH, Stat.RECEPTIONS, D("5.5"), 6, ("3", "01:11"), "(1:11) (Shotgun) 1-J.Hurts pass"),
    (BARKLEY, Stat.RUSHING_YARDS, D("73.5"), 82, ("3", "15:00"), "(15:00) 26-S.Barkley"),
    (SMITH, Stat.LONGEST_RECEPTION, D("29.5"), 30, ("2", "01:12"), "(1:12) (No Huddle, Shotgun)"),
])
def test_the_recorded_game(pbp, athlete, stat, line, final, when, starts):
    data, plays = pbp
    gsis = data.gsis_id(athlete)
    vals = [play_value(row, gsis, stat) for row in plays]
    assert total(vals, stat) == final
    row = plays[deciding_index(vals, stat, line)]
    assert (row["qtr"], row["time"]) == when and row["desc"].startswith(starts)


def test_plays_come_in_order_and_other_games_are_dropped(pbp):
    data, plays = pbp
    assert len(plays) == 155
    ids = [D(r["play_id"]) for r in plays]
    assert ids == sorted(ids)
    assert data.plays(2026, frozenset({"2099_01_NOT_REAL"})) == {"2099_01_NOT_REAL": []}
