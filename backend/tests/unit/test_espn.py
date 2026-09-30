"""ESPN parsers against recorded fixtures, and fetching with host failover (SPEC.md 6.1)."""
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from juju.core.enums import EventStatus, Sport
from juju.ingest import espn

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "espn"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


def games(sport: Sport, name: str) -> dict[str, espn.Game]:
    result = espn.parse_scoreboard(sport, load(name))
    assert result.errors == {}
    return {g.espn_event_id: g for g in result.games}


# --- Scoreboards ---------------------------------------------------------------------------


def test_nfl_final_scoreboard():
    by_id = games(Sport.NFL, "nfl_scoreboard_2026-09-27_final.json")
    assert len(by_id) == 14
    g = by_id["401872958"]
    assert (g.label, g.away_score, g.home_score) == ("ARI @ SF", 30, 36)
    assert (g.home.espn_id, g.home.name) == ("25", "San Francisco 49ers")
    assert g.status is EventStatus.FINAL
    assert g.start_time == datetime(2026, 9, 27, 20, 5, tzinfo=UTC)


def test_sunday_night_game_belongs_to_sunday():
    g = games(Sport.NFL, "nfl_scoreboard_2026-09-27_final.json")["401872962"]
    assert g.start_time == datetime(2026, 9, 28, 0, 20, tzinfo=UTC)
    assert espn.game_day(g.start_time) == date(2026, 9, 27)


def test_overtime_final():
    g = games(Sport.NFL, "nfl_scoreboard_2026-09-13_overtime.json")["401872923"]
    assert (g.status, g.period, g.status_detail) == (EventStatus.FINAL, 5, "Final/OT")


def test_scheduled_game_has_no_scores():
    g = games(Sport.NFL, "nfl_scoreboard_2026-09-28_scheduled.json")["401872963"]
    assert g.status is EventStatus.SCHEDULED
    assert (g.home_score, g.away_score) == (None, None)
    assert g.label == "PHI @ CHI"


def test_games_are_sorted_by_start_time():
    result = espn.parse_scoreboard(Sport.NFL, load("nfl_scoreboard_2026-09-27_final.json"))
    starts = [g.start_time for g in result.games]
    assert starts == sorted(starts)


def test_a_broken_event_does_not_stop_the_others():
    payload = load("nfl_scoreboard_2026-09-27_final.json")
    del payload["events"][0]["competitions"]
    payload["events"][1]["status"]["type"]["state"] = "sideways"
    result = espn.parse_scoreboard(Sport.NFL, payload)
    assert len(result.games) == 12
    assert set(result.errors) == {payload["events"][0]["id"], payload["events"][1]["id"]}


@pytest.mark.parametrize("payload", [None, [], {"leagues": []}, {"events": "none"}])
def test_malformed_scoreboard_is_a_schema_error(payload):
    with pytest.raises(espn.SchemaError):
        espn.parse_scoreboard(Sport.NFL, payload)


@pytest.mark.parametrize(
    ("name", "state", "completed", "expected"),
    [
        ("STATUS_SCHEDULED", "pre", False, EventStatus.SCHEDULED),
        ("STATUS_IN_PROGRESS", "in", False, EventStatus.IN_PROGRESS),
        ("STATUS_HALFTIME", "in", False, EventStatus.BREAK),
        ("STATUS_END_PERIOD", "in", False, EventStatus.BREAK),
        ("STATUS_DELAYED", "in", False, EventStatus.DELAYED),
        ("STATUS_RAIN_DELAY", "pre", False, EventStatus.DELAYED),
        ("STATUS_FINAL", "post", True, EventStatus.FINAL),
        ("STATUS_FINAL_PEN", "post", True, EventStatus.FINAL),
        ("STATUS_POSTPONED", "post", False, EventStatus.POSTPONED),
        ("STATUS_CANCELED", "post", False, EventStatus.CANCELLED),
        ("STATUS_SOMETHING_NEW", "in", False, EventStatus.IN_PROGRESS),
        ("STATUS_SOMETHING_NEW", "post", False, EventStatus.POSTPONED),
    ],
)
def test_status_mapping(name, state, completed, expected):
    assert espn.map_status(name, state, completed) is expected


# --- Rosters -------------------------------------------------------------------------------


def test_nfl_roster_is_grouped_and_puts_unavailable_players_last():
    players = espn.parse_roster(load("nfl_roster_22.json"))
    assert len(players) == 82
    assert {p.group for p in players} >= {"offense", "defense", "injuredReserveOrOut"}
    flags = [p.unavailable for p in players]
    assert flags == sorted(flags)  # available first
    assert all(p.unavailable for p in players if p.group == "injuredReserveOrOut")


def test_malformed_roster_is_a_schema_error():
    with pytest.raises(espn.SchemaError):
        espn.parse_roster({"athletes": [{"items": [{"fullName": "No Id"}]}]})
