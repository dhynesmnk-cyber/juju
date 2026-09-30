"""The next-day check against nflverse, end to end on the recorded PHI @ CHI game: the real ESPN
box score, the real nflverse slice, and the cards the API builds from the result."""
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from juju.api.views import Check, check_notes, player_check, player_view, team_view
from juju.config import DEFAULT_BOOK_CHAIN
from juju.core.card import Outcome
from juju.core.enums import DataSource, FailureKind, Stat
from juju.core.markets import BY_KEY
from juju.core.models import Game, LiveStat, StatCorrection
from juju.dev_seed import RECORDED_KICKOFF, seed_phi_chi_scenario
from juju.ingest import espn
from juju.ingest.http import FetchError
from juju.ingest.nflverse import CHECKED_STATS, MAX_STATS, NflverseData
from juju.ingest.router import Breakers
from juju.worker.live import write_box
from juju.worker.verify import VerifyGames
from tests.support import BARKLEY, FIXTURES, SMITH, load, nflverse_loader

pytestmark = pytest.mark.db

FINAL_AT = RECORDED_KICKOFF + timedelta(hours=3, minutes=10)
NEXT_MORNING = datetime.fromisoformat("2026-09-29T14:07:00+00:00")  # 10:07 ET
LINEMEN = 4  # in nflverse only: each had a penalty, so they played (zero stats, not "no line")


def seeded(db) -> int:
    with Session(db, expire_on_commit=False) as s:
        return seed_phi_chi_scenario(s, "final", FINAL_AT + timedelta(minutes=30)).id


def run(db, now=NEXT_MORNING, loader=None, calls=None):
    breakers = Breakers(engine=None)
    made = []

    def make():
        made.append(1)
        return NflverseData(breakers, loader or nflverse_loader(calls=calls))
    out = VerifyGames(db, make)(now)
    return out, bool(made)


def smith_card(db, game_id, key):
    with Session(db) as s:
        view = player_view(s, s.get(Game, game_id), SMITH, NEXT_MORNING, DEFAULT_BOOK_CHAIN)
    return next(c for c in view.cards if c.key == key)


def set_stat(db, game_id, athlete, stat, value):
    with Session(db) as s:
        row = s.get(LiveStat, (game_id, athlete, stat))
        row.value = value
        s.commit()


def test_cards_await_verification_until_the_check_runs(db):
    game_id = seeded(db)
    rec = smith_card(db, game_id, "player_reception_yds")
    assert rec.outcome is Outcome.LOST and rec.verified is False
    assert rec.notes == ["Awaiting verification against the official stats."]


def test_the_recorded_game_verifies_against_nflverse(db):
    game_id = seeded(db)
    out, _ = run(db)
    assert (out.verified, out.waiting, out.disputed) == (1, 0, 0)
    assert out.corrected == LINEMEN * len(CHECKED_STATS)  # their zeros, nothing else
    with Session(db) as s:
        game = s.get(Game, game_id)
        assert game.verified_at == NEXT_MORNING and game.last_error is None
        rows = s.scalars(select(LiveStat).where(LiveStat.game_id == game_id)).all()
        espn_rows = [r for r in rows if r.source is not DataSource.NFLVERSE]
        # Every ESPN value nflverse covers agreed, the longest plays through the play-by-play.
        assert all(r.verified_at == NEXT_MORNING for r in espn_rows
                   if r.stat in CHECKED_STATS | MAX_STATS)
        assert all(r.verified_at is None for r in espn_rows
                   if r.stat not in CHECKED_STATS | MAX_STATS)
        corrections = s.scalars(select(StatCorrection)).all()
        assert {c.old_value for c in corrections} == {None}
        assert {c.new_value for c in corrections} == {D(0)}
    rec = smith_card(db, game_id, "player_reception_yds")
    assert rec.verified is True and rec.outcome is Outcome.LOST
    assert rec.notes == ["Verified against the official stats (nflverse)."]
    with Session(db) as s:
        team = team_view(s, s.get(Game, game_id), "3", NEXT_MORNING, DEFAULT_BOOK_CHAIN)
    spread = next(c for c in team.cards if c.key == "spreads")
    assert spread.verified is True and spread.outcome is Outcome.WON


def test_a_game_is_checked_once(db):
    seeded(db)
    run(db)
    calls = []
    out, made = run(db, NEXT_MORNING + timedelta(hours=6), calls=calls)
    assert out.verified == 0 and not made and calls == []  # nothing due: nothing downloaded


def test_a_disagreement_reopens_the_payout_with_the_official_number(db):
    game_id = seeded(db)
    set_stat(db, game_id, SMITH, Stat.RECEPTIONS, D(5))  # the feed says 5; he caught 6
    assert smith_card(db, game_id, "player_receptions").outcome is Outcome.LOST  # 5 < 5.5
    out, _ = run(db)
    assert out.corrected == LINEMEN * len(CHECKED_STATS) + 1
    card = smith_card(db, game_id, "player_receptions")
    assert card.outcome is Outcome.WON and card.current == 6 and card.verified is True
    assert card.headline.startswith("$10 → $") and not card.headline.endswith("$0.00")
    assert card.notes == [
        "Corrected after the game: the official stats have 6; the live feed had 5.",
        "Verified against the official stats (nflverse)."]
    with Session(db) as s:
        row = s.get(LiveStat, (game_id, SMITH, Stat.RECEPTIONS))
        assert (row.value, row.source) == (6, DataSource.NFLVERSE)
        assert s.get(Game, game_id).corrected_at == NEXT_MORNING


def test_a_later_box_score_never_overwrites_the_official_number(db):
    game_id = seeded(db)
    set_stat(db, game_id, SMITH, Stat.RECEPTIONS, D(5))
    run(db)
    summary = load(FIXTURES / "espn" / "nfl_summary_401872963_full.json")
    box = espn.parse_box_score(summary)
    stale = replace(box, stats={**box.stats, Stat.RECEPTIONS: {
        **box.stats[Stat.RECEPTIONS], SMITH: D(5)}})
    with Session(db) as s:
        game = s.get(Game, game_id)
        write_box(s, game, stale, DataSource.ESPN_WEB, NEXT_MORNING + timedelta(hours=1))
        s.commit()
        assert s.get(LiveStat, (game_id, SMITH, Stat.RECEPTIONS)).value == 6


def test_a_box_score_change_after_settling_is_recorded(db):
    game_id = seeded(db)
    summary = load(FIXTURES / "espn" / "nfl_summary_401872963_full.json")
    box = espn.parse_box_score(summary)
    changed = replace(box, stats={**box.stats, Stat.RECEIVING_YARDS: {
        **box.stats[Stat.RECEIVING_YARDS], SMITH: D(72)}})
    later = FINAL_AT + timedelta(hours=1)
    with Session(db) as s:
        write_box(s, s.get(Game, game_id), changed, DataSource.ESPN_SITE, later)
        s.commit()
        c = s.scalars(select(StatCorrection)).one()
        assert (c.espn_athlete_id, c.stat, c.old_value, c.new_value, c.source) == (
            SMITH, Stat.RECEIVING_YARDS, 65, 72, DataSource.ESPN_SITE)
        assert s.get(Game, game_id).corrected_at == later
    card = smith_card(db, game_id, "player_reception_yds")
    assert card.outcome is Outcome.WON  # 72 > 71.5
    assert card.notes[0] == ("Corrected after the game: a later box score has 72; the live "
                             "feed had 65.")


def test_a_first_box_score_after_settling_is_not_a_correction(db):
    game_id = seeded(db)
    with Session(db) as s:
        s.execute(LiveStat.__table__.delete())
        s.commit()
    box = espn.parse_box_score(load(FIXTURES / "espn" / "nfl_summary_401872963_full.json"))
    with Session(db) as s:
        write_box(s, s.get(Game, game_id), box, DataSource.ESPN_WEB, FINAL_AT + timedelta(hours=1))
        s.commit()
        assert s.scalar(select(func.count()).select_from(StatCorrection)) == 0
        assert s.get(Game, game_id).corrected_at is None
    # A player first listed once the game already had a box score is one: his card changes.
    newcomer = replace(box, appeared=box.appeared | {"999"},
                       stats={**box.stats, Stat.RECEPTIONS: {**box.stats[Stat.RECEPTIONS],
                                                             "999": D(1)}})
    with Session(db) as s:
        write_box(s, s.get(Game, game_id), newcomer, DataSource.ESPN_WEB,
                  FINAL_AT + timedelta(hours=2))
        s.commit()
        c = s.scalars(select(StatCorrection).where(StatCorrection.stat == Stat.RECEPTIONS)).one()
        assert (c.espn_athlete_id, c.old_value, c.new_value) == ("999", None, 1)


def test_a_disputed_final_score_applies_nothing(db):
    game_id = seeded(db)
    with Session(db) as s:
        s.get(Game, game_id).home_score = 28
        s.commit()
    out, _ = run(db)
    assert (out.verified, out.disputed, out.corrected) == (0, 1, 0)
    with Session(db) as s:
        game = s.get(Game, game_id)
        assert game.verified_at is None and "27-7" in game.last_error
        assert s.scalar(select(func.count()).select_from(LiveStat).where(
            LiveStat.verified_at.is_not(None))) == 0


def test_a_game_nflverse_has_not_published_waits(db):
    game_id = seeded(db)
    header = (FIXTURES / "nflverse" / "stats_player_week_2026.csv").read_text().splitlines()[0]
    out, _ = run(db, loader=nflverse_loader(replace={"player_stats": header.encode()}))
    assert (out.verified, out.waiting) == (0, 1)
    with Session(db) as s:
        assert s.get(Game, game_id).verified_at is None


@pytest.mark.parametrize("now", [
    FINAL_AT + timedelta(hours=1),              # too soon after the final whistle
    RECORDED_KICKOFF + timedelta(days=7, minutes=1),  # older than a week
])
def test_only_games_in_the_window_are_checked(db, now):
    seeded(db)
    out, made = run(db, now)
    assert out.verified == 0 and not made


def test_a_failed_download_stops_the_run_and_changes_nothing(db):
    game_id = seeded(db)

    def failing(dataset, season):
        raise FetchError("https://github.com/x", FailureKind.TRANSIENT, "timeout")
    out, made = run(db, loader=failing)
    assert made and out.verified == 0
    with Session(db) as s:
        assert s.get(Game, game_id).verified_at is None


def test_the_longest_plays_are_confirmed_by_the_play_by_play_never_corrected(db):
    game_id = seeded(db)
    set_stat(db, game_id, SMITH, Stat.LONGEST_RECEPTION, D(31))  # the feed says 31; it was 30
    run(db)
    with Session(db) as s:
        smith = s.get(LiveStat, (game_id, SMITH, Stat.LONGEST_RECEPTION))
        assert (smith.value, smith.verified_at, smith.source) == (31, None, DataSource.ESPN_WEB)
        barkley = s.get(LiveStat, (game_id, BARKLEY, Stat.LONGEST_RUSH))
        assert barkley.verified_at == NEXT_MORNING
        assert s.scalar(select(func.count()).select_from(StatCorrection).where(
            StatCorrection.stat == Stat.LONGEST_RECEPTION)) == 0
        game = s.get(Game, game_id)
        rows = {r.stat: r for r in s.scalars(select(LiveStat).where(
            LiveStat.game_id == game_id, LiveStat.espn_athlete_id == SMITH))}
        check = player_check(BY_KEY["player_reception_longest"], game, rows, [], first_td=None)
        assert check_notes(check, Outcome.WON) == (False, [
            "The official stats don't confirm this number, so it isn't verified."])


def test_notes_for_a_stat_the_official_stats_do_not_have(db):
    game_id = seeded(db)
    run(db)
    with Session(db) as s:
        game = s.get(Game, game_id)
        rows = {r.stat: r for r in s.scalars(select(LiveStat).where(
            LiveStat.game_id == game_id, LiveStat.espn_athlete_id == SMITH))}
        check = player_check(BY_KEY["player_pass_longest_completion"], game, rows, [],
                             first_td=None)
        assert check == Check(checkable=False)  # no stat for it at all
        assert check_notes(check, Outcome.WON) == (
            False, ["The official stats don't include this stat, so it can't be verified."])
        # Nothing is said about a void, or before a game settles.
        receptions = player_check(BY_KEY["player_receptions"], game, rows, [], first_td=None)
        assert check_notes(receptions, Outcome.VOID) == (False, [])
        assert check_notes(Check(True), Outcome.LIVE) == (False, [])
        # No stat line in ESPN or nflverse: confirmed by the check.
        assert player_check(BY_KEY["player_receptions"], game, {}, [], first_td=None).verified

