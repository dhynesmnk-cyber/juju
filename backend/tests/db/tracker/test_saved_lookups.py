"""Saving the bet pages owners open (juju/tracker/saved.py): what makes two the same, and the
upsert that bumps one already saved."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from juju.core.enums import EventStatus
from juju.core.models import Game
from juju.tracker import saved
from juju.tracker.saved import Asked

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 18, 17, tzinfo=UTC)


def test_what_makes_two_lookups_the_same():
    sig = saved.signature
    assert sig(Asked("player", "1", market_key="player_rush_yds", threshold=Decimal("79.5"))) \
        == "player|1|player_rush_yds|79.5|"
    assert sig(Asked("player", "1", threshold=Decimal("80.0"))) == "player|1||80|"
    # The length of the play and which play was tapped don't make another lookup.
    assert sig(Asked("player", "1", play="catch", yards=30, play_id=4)) == \
        sig(Asked("player", "1", play="catch", yards=45))
    # A parlay's legs in any order.
    assert sig(Asked("parlay", "t-3-spreads,p-1-player_rush_yds")) == \
        sig(Asked("parlay", "p-1-player_rush_yds,t-3-spreads"))
    assert sig(Asked("team", "3")) != sig(Asked("player", "3"))


@pytest.fixture
def game(session) -> Game:
    g = Game(home_team="Chicago Bears", away_team="Philadelphia Eagles", home_espn_id="3",
             away_espn_id="21", commence_time=NOW, status=EventStatus.SCHEDULED)
    session.add(g)
    session.flush()
    return g


def test_remember_bumps_the_one_already_saved(session, game):
    first, new = saved.remember(session, "chris", game.id, Asked("player", "1", yards=30), NOW)
    assert new
    later = NOW + timedelta(minutes=5)
    again, new = saved.remember(session, "chris", game.id, Asked("player", "1"), later)
    assert (again, new) == (first, False)
    other, new = saved.remember(session, "ash", game.id, Asked("player", "1"), later)
    assert new and other != first  # each owner their own
    (row,) = saved.for_game(session, "chris", game.id, limit=10)
    assert (row.times, row.yards, row.first_seen_at, row.last_seen_at) == (2, 30, NOW, later)


def test_forget_removes_only_the_owner_s_own(session, game):
    lookup, _ = saved.remember(session, "chris", game.id, Asked("team", "3"), NOW)
    assert not saved.forget(session, "ash", lookup)
    assert saved.forget(session, "chris", lookup)
    assert saved.for_game(session, "chris", game.id, limit=10) == []


def test_a_game_s_latest_lookups_in_the_order_opened(session, game):
    for n in range(5):
        saved.remember(session, "chris", game.id, Asked("player", str(n)),
                       NOW + timedelta(minutes=n))
    assert [r.target for r in saved.for_game(session, "chris", game.id, limit=3)] == \
        ["2", "3", "4"]
