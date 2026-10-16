"""How credits are shared out over the games left (core/budget.py)."""
from datetime import datetime

import pytest

from juju.core import budget
from juju.core.budget import FULL_COST, T45_COST, Tier
from juju.core.markets import ALL_KEYS, CAPTURE_PRIORITY


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s + "+00:00")


def test_every_market_has_a_place_in_the_priority_list():
    assert sorted(CAPTURE_PRIORITY) == sorted(ALL_KEYS)
    assert CAPTURE_PRIORITY[:3] == ("h2h", "spreads", "totals")


@pytest.mark.parametrize("now, day, reset", [
    ("2026-10-15T22:00:00", 1, "2026-11-01T00:00:00"),
    ("2026-10-15T22:00:00", 20, "2026-10-20T00:00:00"),
    ("2026-10-20T00:00:00", 20, "2026-11-20T00:00:00"),  # the moment it resets: the next one
    ("2026-12-31T23:59:00", 1, "2027-01-01T00:00:00"),
])
def test_next_reset(now, day, reset):
    assert budget.next_reset(utc(now), day) == utc(reset)


@pytest.mark.parametrize("now, day, reset", [
    ("2026-10-15T22:00:00", 1, "2026-10-01T00:00:00"),
    ("2026-10-15T22:00:00", 20, "2026-09-20T00:00:00"),
    ("2026-10-20T00:00:00", 20, "2026-10-20T00:00:00"),  # the moment it resets: this one
    ("2027-01-10T08:00:00", 15, "2026-12-15T00:00:00"),
])
def test_last_reset(now, day, reset):
    at = utc(now)
    assert budget.last_reset(at, day) == utc(reset)
    assert budget.last_reset(at, day) <= at < budget.next_reset(at, day)


@pytest.mark.parametrize("read, trusted", [
    (None, False),                   # nobody said when: a count from before 0009
    ("2026-09-30T23:59:00", False),  # last month's
    ("2026-10-01T00:00:00", True),   # read at the reset
    ("2026-10-14T18:00:00", True),
])
def test_a_count_read_before_the_reset_is_not_trusted(read, trusted):
    now = utc("2026-10-15T22:00:00")
    count = budget.current_quota(0, read and utc(read), now, 1)
    assert count == (0 if trusted else None)
    assert budget.current_quota(None, now, now, 1) is None


def test_games_left_is_never_fewer_than_a_full_schedule():
    now, reset = utc("2026-10-15T22:00:00"), utc("2026-11-01T00:00:00")  # 16 days and 2 hours
    assert budget.games_left(3, now, reset, 16) == 37  # the vendor lists only a few days ahead
    assert budget.games_left(40, now, reset, 16) == 40
    assert budget.games_left(0, reset, reset, 16) == 1


@pytest.mark.parametrize("quota, games, tier, markets", [
    (None, 50, Tier.FULL, len(ALL_KEYS)),  # unknown until the first response
    (20_000, 70, Tier.FULL, len(ALL_KEYS)),
    (FULL_COST * 10, 10, Tier.FULL, len(ALL_KEYS)),
    (FULL_COST * 10 - 1, 10, Tier.T45_ONLY, len(ALL_KEYS)),
    (T45_COST * 10, 10, Tier.T45_ONLY, len(ALL_KEYS)),
    (500, 70, Tier.LEAN, 7),  # a free key's month
    (30, 10, Tier.LEAN, 3),
    (29, 10, Tier.NONE, 0),
    (0, 10, Tier.NONE, 0),
    (-5, 10, Tier.NONE, 0),
])
def test_the_best_plan_that_fits(quota, games, tier, markets):
    plan = budget.plan(quota, games)
    assert plan.tier is tier and len(plan.markets) == markets
    assert plan.early is (tier is Tier.FULL)
    assert plan.markets == (ALL_KEYS if markets == len(ALL_KEYS) else CAPTURE_PRIORITY[:markets])
    assert (plan.note is not None) is (tier is Tier.NONE)
