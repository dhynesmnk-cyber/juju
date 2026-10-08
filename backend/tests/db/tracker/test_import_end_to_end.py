# Ported from parlaytracker@c3bd43c tests/db/test_import_end_to_end.py. Changes: imports; the four
# cases that settle through the worker's jobs (CheckFinals, Settle, the backfill) wait for those
# jobs (Phase 2); and one new case settles a leg by hand to show check-import's exit code.
"""Importing transcribed slips end to end: written through `services`, settled by the same jobs
as any slip, then compared with what the sportsbook said (ARI @ SF, recorded)."""
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tests.support import FIXTURES, load

from juju import cli
from juju.ingest import espn
from juju.tracker import services, slip_import
from juju.tracker.models import (
    Event,
    EventStatus,
    LegResult,
    MarketType,
    Slip,
    SlipStatus,
    SlipType,
    Sport,
    Sportsbook,
)
from tests.tracker_support import HEADER, ROSTERS, csv_text, row, two_legs

pytestmark = pytest.mark.db

ARI_SF = "401872958"  # ESPN's id for the recorded ARI @ SF game
WEEKS_LATER = datetime(2026, 10, 17, tzinfo=UTC)
BOARD = espn.parse_scoreboard(
    Sport.NFL, load(FIXTURES / "espn" / "nfl_scoreboard_2026-09-27_final.json"))
USER = "user1@example.com"


@pytest.fixture(autouse=True)
def no_leftover_hard_rock(engine):
    """`clean` doesn't truncate sportsbooks, and these tests add (or expect to add) one."""
    def remove():
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sportsbooks WHERE name LIKE 'Hard Rock%'"))
    remove()
    yield
    remove()


def games_for(sport, day):
    return BOARD.games if day.isoformat() == "2026-09-27" else []


def roster_for(sport, team_id):
    return ROSTERS[team_id]


def planned(session, rows):
    return slip_import.plan_import(session, slip_import.parse_csv(csv_text(rows)),
                                   games_for=games_for, roster_for=roster_for)


def test_apply_writes_the_slip_through_the_services_with_its_original_date(engine, clean):
    rows = two_legs(boost_pct="33", placed_at_iso="2026-09-27T12:00:00-04:00")
    with Session(engine) as s:
        plans = planned(s, rows)
        assert any("will be added" in n for n in plans[0].notes)
        report = slip_import.apply_plans(s, plans, USER)
        s.commit()
    assert report.imported == ["111"] and report.skipped == {}
    with Session(engine) as s:
        slip = s.scalars(select(Slip)).one()
        assert (slip.slip_type, slip.american_odds, slip.stake, slip.boosted, slip.is_placed) == (
            SlipType.SGP, 450, D("40.00"), True, True)
        assert slip.logged_by == USER and slip.notes == "import Hard Rock #111"
        assert slip.source.value == "screenshot"
        assert slip.created_at == datetime(2026, 9, 27, 16, 0, tzinfo=UTC)  # 12:00 ET
        assert (slip.status, slip.payout) == (SlipStatus.PENDING, None)
        assert [(leg.market_type, leg.espn_athlete_id, leg.player_name, leg.line,
                 leg.american_odds) for leg in slip.legs] == [
            (MarketType.PLAYER_RECEPTIONS, "4361307", "Trey McBride", D("9.5"), None),
            (MarketType.PLAYER_TOUCHDOWNS, "3040151", "George Kittle", D("0.5"), None)]
        book = s.get(Sportsbook, slip.sportsbook_id)
        assert (book.name, book.odds_api_key) == ("Hard Rock", "hardrockbet")
        event = s.scalars(select(Event)).one()
        # Not "final": the worker finds a finished game final and dates it, so the ten-minute
        # gate holds (section 7.1).
        assert event.status is EventStatus.SCHEDULED and event.espn_event_id == ARI_SF


def test_importing_twice_adds_nothing(engine, clean):
    for _ in range(2):
        with Session(engine) as s:
            plans = planned(s, two_legs())
            report = slip_import.apply_plans(s, plans, USER)
            s.commit()
    assert report.skipped == {"111": "already imported"} and report.imported == []
    with Session(engine) as s:
        assert len(s.scalars(select(Slip)).all()) == 1
        assert len(s.scalars(select(Sportsbook).where(Sportsbook.name == "Hard Rock")).all()) == 1


def test_an_existing_sportsbook_is_reused_by_its_printed_name(engine, clean):
    with Session(engine) as s:
        s.add(Sportsbook(name="Hard Rock Bet", odds_api_key="hardrockbet"))
        s.commit()
    with Session(engine) as s:
        slip_import.apply_plans(s, planned(s, two_legs()), USER)
        s.commit()
        assert len(s.scalars(select(Sportsbook).where(Sportsbook.name.like("Hard Rock%")))
                   .all()) == 1


def test_errors_and_doubts_are_skipped_unless_doubts_are_allowed(engine, clean):
    rows = (two_legs(slip_id="ok") + two_legs(slip_id="bad", matchup="Nobody vs Nobody")
            + two_legs(slip_id="doubt", placed_at_iso="2026-09-27T17:00:00-04:00"))
    with Session(engine) as s:
        report = slip_import.apply_plans(s, planned(s, rows), USER)
        s.commit()
    assert report.imported == ["ok"]
    assert report.skipped == {"bad": "error", "doubt": "doubtful"}
    with Session(engine) as s:
        report = slip_import.apply_plans(s, planned(s, rows), USER, include_doubtful=True)
        s.commit()
        assert report.imported == ["doubt"]
        assert report.skipped == {"ok": "already imported", "bad": "error"}


def test_one_slip_the_database_rejects_does_not_stop_the_rest(engine, clean):
    dup = two_legs(slip_id="dup")
    dup[1].update(player="Trey McBride", market="receptions", line="9.5",
                  raw_text="TREY MCBRIDE - RECEPTIONS")  # the same selection twice
    with Session(engine) as s:
        report = slip_import.apply_plans(s, planned(s, dup + two_legs(slip_id="fine")), USER)
        s.commit()
    assert report.imported == ["fine"]
    assert report.skipped["dup"].startswith("rejected:")


def sportsbook_rows():
    # McBride 9 receptions, Kittle 2 TDs, Brissett 38 completions, Ryland 3 FGs (ESPN's box)
    return [
        row(slip_id="a", leg_seq="1", player="Trey McBride", market="receptions", line="8.5",
            result="won", raw_text="TREY MCBRIDE - RECEPTIONS", leg_count="2", status="won",
            paid="220.10"),
        row(slip_id="a", leg_seq="2", player="George Kittle", market="touchdowns", line="1.5",
            result="won", raw_text="GEORGE KITTLE - TO SCORE 2+ TDS", leg_count="2",
            status="won", paid="220.10"),
        row(slip_id="b", leg_seq="1", player="Jacoby Brissett", market="pass_completions",
            line="38.5", result="lost", raw_text="JACOBY BRISSETT - PASS COMPLETIONS",
            leg_count="2"),
        row(slip_id="b", leg_seq="2", player="Chad Ryland", market="field_goals_made",
            line="2.5", result="won", raw_text="CHAD RYLAND - FIELD GOALS MADE", leg_count="2"),
    ]


def test_legs_not_yet_settled_are_counted_not_reported(engine, clean):
    rows = sportsbook_rows()
    with Session(engine) as s:
        slip_import.apply_plans(s, planned(s, rows), USER)
        s.commit()
        report = slip_import.check_import(s, slip_import.parse_csv(csv_text(rows)))
    assert (report.legs_pending, report.legs_agree, report.mismatches) == (4, 0, [])


def test_a_slip_that_was_never_imported_is_listed(engine, clean):
    with Session(engine) as s:
        report = slip_import.check_import(s, slip_import.parse_csv(csv_text(two_legs())))
    assert report.not_imported == ["111"]


# --- the commands -----------------------------------------------------------------------------


def write_csv(tmp_path, rows):
    path = tmp_path / "slips.csv"
    path.write_text(csv_text(rows))
    return path


def test_the_command_is_a_dry_run_unless_told_to_apply(engine, clean, tmp_path, capsys):
    path = write_csv(tmp_path, two_legs())
    assert cli.import_slips(engine, path, USER, False, False, games_for, roster_for) == 0
    out = capsys.readouterr().out
    assert "OK        111" in out and "nothing was written" in out
    with Session(engine) as s:
        assert s.scalars(select(Slip)).all() == []
    assert cli.import_slips(engine, path, USER, True, False, games_for, roster_for) == 0
    assert "imported 1 slip(s)" in capsys.readouterr().out
    with Session(engine) as s:
        assert len(s.scalars(select(Slip)).all()) == 1


def test_a_file_that_cannot_be_read_changes_nothing(engine, clean, tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_text(HEADER.replace("wager", "stake") + "\n")
    assert cli.import_slips(engine, path, USER, True, False, games_for, roster_for) == 1
    assert "missing column(s): wager" in capsys.readouterr().err
    assert cli.import_slips(engine, tmp_path / "nope.csv", USER, True, False,
                            games_for, roster_for) == 1


def test_a_dash_reads_the_csv_from_standard_input(engine, clean, monkeypatch, capsys):
    import io
    from pathlib import Path

    monkeypatch.setattr("sys.stdin", io.StringIO(csv_text(two_legs())))
    assert cli.import_slips(engine, Path("-"), USER, False, False, games_for, roster_for) == 0
    assert "OK        111" in capsys.readouterr().out


def test_check_import_exits_nonzero_on_a_mismatch(engine, clean, tmp_path, capsys):
    """New in Juju: the leg is settled by hand here (parlaytracker's case settles it through the
    worker's jobs, which arrive in Phase 2)."""
    path = write_csv(tmp_path, two_legs())  # the sportsbook said lost
    assert cli.import_slips(engine, path, USER, True, False, games_for, roster_for) == 0
    assert cli.check_import(engine, path) == 0  # nothing settled yet: nothing to disagree with
    capsys.readouterr()
    with Session(engine) as s:
        leg = s.scalars(select(Slip)).one().legs[0]
        services.settle_leg_manually(s, leg, result=LegResult.WIN, now=WEEKS_LATER)
        s.commit()
    assert cli.check_import(engine, path) == 1
    # A result entered by hand has no final value to quote (parlaytracker crashed here).
    assert ("MISMATCH 111: leg 1 Trey McBride receptions over 9.5: the slip says lost, "
            "we settled win\n") in capsys.readouterr().out


def test_the_commands_are_reached_from_the_command_line(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_engine", lambda: "engine")
    monkeypatch.setattr(cli, "import_slips", lambda *a: calls.append(("import", *a)) or 0)
    monkeypatch.setattr(cli, "check_import", lambda *a: calls.append(("check", *a)) or 0)
    assert cli.main(["import-slips", "--user", "alice", "slips.csv", "--apply"]) == 0
    assert cli.main(["import-slips", "-", "--user", "bob", "--include-doubtful"]) == 0
    assert cli.main(["check-import", "slips.csv"]) == 0
    assert cli.main(["import-slips", "slips.csv"]) == 2  # no --user: the usage
    assert calls == [
        ("import", "engine", Path("slips.csv"), "alice", True, False),
        ("import", "engine", Path("-"), "bob", False, True),
        ("check", "engine", Path("slips.csv")),
    ]
