"""Lookups on a Sunday-like slate: two games on at once, namesakes in both, plays that just
happened, and text that names no one (api/lookup.py, api/views.py `the_play`)."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from juju.api.lookup import resolve
from juju.api.views import Focus, player_view
from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.enums import EventStatus
from juju.core.models import Game, Play
from juju.dev_seed import seed_phi_chi_scenario
from juju.ingest import espn
from juju.worker.schedule import upsert_roster
from tests.support import FIXTURES, SMITH

pytestmark = pytest.mark.db

ROMAN_WILSON, JOHNNY_WILSON = "4431492", "4686104"   # PIT WR, PHI WR
DANIEL_HARDY, DAEQUAN_HARDY = "4365629", "4429743"   # CHI DE, PIT CB
HURTS = "4040715"


@pytest.fixture
def slate(db):
    """PHI @ CHI in play (the recorded game), and PIT @ CLE: in play too, or final
    `final_ago`. Build it once per test; its session is closed afterwards."""
    now = datetime.now(tz=UTC)
    s = Session(db, expire_on_commit=False)

    def make(pit_cle: str = "live", final_ago: timedelta = timedelta(minutes=20)):
        phi = seed_phi_chi_scenario(s, "live", now)
        for team in ("23", "5"):
            roster = json.loads((FIXTURES / "espn" / f"nfl_roster_{team}.json").read_text())
            upsert_roster(s, team, espn.parse_roster(roster), now)
        pit = Game(home_team="Cleveland Browns", away_team="Pittsburgh Steelers",
                   home_abbr="CLE", away_abbr="PIT", home_espn_id="5", away_espn_id="23",
                   espn_event_id="401872964", commence_time=now - timedelta(hours=3),
                   status=EventStatus.IN_PROGRESS, period=4)
        if pit_cle == "final":
            pit.status, pit.final_at = EventStatus.FINAL, now - final_ago
        s.add(pit)
        s.commit()
        return s, phi.id, pit.id, now
    yield make
    s.close()


def _play(db, game_id, athlete, kind, label, ago: timedelta, *, passer=None, yards=None,
          team=None) -> int:
    with Session(db) as s:
        p = Play(game_id=game_id, espn_play_id=f"test-{label}", sequence=9999, period=4,
                 clock="2:00", wallclock=datetime.now(tz=UTC) - ago, kind=kind, label=label,
                 yards=yards, text=label, espn_athlete_id=athlete, team_espn_id=team,
                 scoring=kind == "touchdown", passer_espn_id=passer)
        s.add(p)
        s.commit()
        return p.id


def test_namesakes_in_two_games_are_asked_about(slate, db):
    s, _, _, now = slate()
    r = resolve(s, "Wilson TD", now)
    assert r.kind == "choices"
    assert {ROMAN_WILSON, JOHNNY_WILSON} <= {c.id for c in r.choices}


def test_the_namesake_who_just_scored_is_the_one_meant(slate, db):
    s, _, pit, now = slate()
    _play(db, pit, ROMAN_WILSON, "touchdown", "R. Wilson 22-yd TD catch", timedelta(minutes=3))
    s.commit()  # see the play
    r = resolve(s, "Wilson TD", now)
    assert (r.kind, r.id, r.path) == ("player", ROMAN_WILSON, "recent")


def test_a_play_long_ago_or_of_another_kind_decides_nothing(slate, db):
    s, _, pit, now = slate()
    _play(db, pit, ROMAN_WILSON, "touchdown", "R. Wilson TD catch", timedelta(minutes=30))
    _play(db, pit, ROMAN_WILSON, "catch", "R. Wilson 25-yd catch", timedelta(minutes=2))
    s.commit()
    assert resolve(s, "Wilson TD", now).kind == "choices"


def test_a_first_name_alone_is_still_never_picked_even_after_a_play(slate, db):
    s, _, pit, now = slate()
    _play(db, pit, ROMAN_WILSON, "touchdown", "R. Wilson 22-yd TD catch", timedelta(minutes=3))
    s.commit()
    assert resolve(s, "Roman TD", now).path != "recent"


def test_a_game_just_over_is_searched_with_the_ones_on(slate, db):
    s, _, _, now = slate("final", final_ago=timedelta(minutes=20))
    r = resolve(s, "Hardy sack", now)
    assert r.kind == "choices"  # before: CHI's Hardy, the only one in a game still on
    assert {DANIEL_HARDY, DAEQUAN_HARDY} <= {c.id for c in r.choices}


def test_a_game_long_over_waits_behind_the_ones_on(slate, db):
    s, _, _, now = slate("final", final_ago=timedelta(hours=3))
    assert resolve(s, "Hardy sack", now).id == DANIEL_HARDY


def test_text_that_names_no_one_offers_the_latest_plays(slate, db):
    s, phi, pit, now = slate()
    newest = _play(db, pit, ROMAN_WILSON, "touchdown", "R. Wilson 22-yd TD catch",
                   timedelta(minutes=1), yards=22)
    s.commit()
    for text in ("what just happened", "that TD", "who just scored"):
        r = resolve(s, text, now)
        assert r.kind == "plays" and r.choices, text
        assert r.choices[0].play_id == newest and r.choices[0].id == ROMAN_WILSON
        assert all("$" not in c.name and "$" not in c.detail for c in r.choices)  # no prices
    tds = resolve(s, "that TD", now).choices
    with Session(db) as s2:
        kinds = {s2.get(Play, c.play_id).kind for c in tds}
    assert kinds == {"touchdown"}
    assert resolve(s, "that 22 yard catch", now).choices[0].play_id == newest


def test_a_touchdown_pass_is_offered_as_its_throwers_too(slate, db):
    s, phi, _, now = slate()
    pid = _play(db, phi, SMITH, "touchdown", "D. Smith 30-yd TD catch", timedelta(minutes=1),
                passer=HURTS, yards=30)
    s.commit()
    rows = [c for c in resolve(s, "what just happened", now).choices if c.play_id == pid]
    assert {(c.id, c.play) for c in rows} == {(SMITH, "touchdown"), (HURTS, "pass_td")}
    thrown = resolve(s, "that TD pass", now).choices
    assert thrown[0].id == HURTS and thrown[0].name == "J. Hurts 30-yd TD pass"


def test_the_tapped_play_is_named_and_decides_which_bets_it_touched(slate, db):
    s, phi, pit, now = slate()
    pid = _play(db, phi, SMITH, "touchdown", "D. Smith 30-yd TD catch", timedelta(minutes=1),
                passer=HURTS, yards=30)
    s.commit()
    game = s.get(Game, phi)
    view = player_view(s, game, SMITH, now, DEFAULT_BOOK_CHAIN, Focus(), pid)
    assert view.the_play["label"] == "D. Smith 30-yd TD catch"
    touched = {c.key for c in view.cards if c.touched}
    assert "player_reception_yds" in touched and "player_rush_yds" not in touched
    qb = player_view(s, game, HURTS, now, DEFAULT_BOOK_CHAIN, Focus(), pid)
    assert qb.the_play is not None
    qb_touched = {c.key for c in qb.cards if c.touched}  # his TD pass: passing bets only
    assert "player_pass_yds" in qb_touched and "player_rush_yds" not in qb_touched
    # Another game's play, or a play that isn't his, is ignored.
    other = _play(db, pit, ROMAN_WILSON, "touchdown", "R. Wilson TD", timedelta(minutes=2))
    s.commit()
    assert player_view(s, game, SMITH, now, DEFAULT_BOOK_CHAIN, Focus(), other).the_play is None
    assert s.scalar(select(Play.id).where(Play.id == other)) == other
