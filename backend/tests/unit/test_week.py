"""The NFL week the home page lists (juju/core/week.py)."""
from datetime import UTC, datetime

import pytest

from juju.core.week import ET, game_week


def et(*args) -> datetime:
    return datetime(*args, tzinfo=ET)


THIS_WEEK = (et(2026, 9, 29, 6), et(2026, 10, 6, 6))
NEXT_WEEK = (et(2026, 10, 6, 6), et(2026, 10, 13, 6))


@pytest.mark.parametrize("now, week", [
    (et(2026, 10, 2, 12), THIS_WEEK),       # Friday noon
    (et(2026, 10, 5, 23, 59), THIS_WEEK),   # Monday night, still this week
    (et(2026, 10, 6, 5, 59), THIS_WEEK),    # Tuesday before 06:00: Monday's game ends
    (et(2026, 10, 6, 6), NEXT_WEEK),        # Tuesday 06:00: the next week
    (et(2026, 10, 6, 6).astimezone(UTC), NEXT_WEEK),  # any zone in, the same week
])
def test_a_week_runs_tuesday_to_tuesday_eastern(now, week):
    assert game_week(now) == week


def test_the_week_the_clocks_change_is_still_tuesday_06_00_on_both_ends():
    start, end = game_week(et(2026, 11, 1, 13))  # the Sunday clocks go back
    assert start.astimezone(UTC) == datetime(2026, 10, 27, 10, tzinfo=UTC)  # EDT
    assert end.astimezone(UTC) == datetime(2026, 11, 3, 11, tzinfo=UTC)     # EST
