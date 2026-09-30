"""Reading free text deterministically, the LLM reply reader, and the roster search."""
from decimal import Decimal as D

import httpx
import pytest

from juju.core.enums import PlayKind as P
from juju.ingest.llm import Budget, LlmReader, parse_reply
from juju.ingest.parse import parse_text
from juju.ingest.resolve import RosterEntry, is_confident, match_abbreviated, rank_players


@pytest.mark.parametrize(("text", "play", "yards", "threshold", "market", "name"), [
    ("Saquon Barkley 60 yd TD run", P.TOUCHDOWN, 60, None, None, "saquon barkley"),
    ("barkley 60-yard touchdown", P.TOUCHDOWN, 60, None, None, "barkley"),
    ("Hurts throws a TD to Smith", P.PASS_TD, None, None, None, "hurts smith"),
    ("DeVonta Smith 38 yd catch", P.CATCH, 38, None, None, "devonta smith"),
    ("Barkley 100+ rush yds", P.RUN, None, D("99.5"), "player_rush_yds", "barkley"),
    ("Smith over 71.5 receiving yards", None, None, D("71.5"), "player_reception_yds",
     "smith"),
    ("Burden anytime td", P.TOUCHDOWN, None, None, "player_anytime_td", "burden"),
    ("Santos 48 yard field goal", P.FIELD_GOAL, 48, None, None, "santos"),
    ("Bears cover", None, None, None, "spreads", "bears"),
    ("Kelce", None, None, None, None, "kelce"),
])
def test_parse_text(text, play, yards, threshold, market, name):
    p = parse_text(text)
    assert (p.play, p.yards, p.threshold, p.market_key, p.name_text) == (
        play, yards, threshold, market, name)


def test_long_text_is_cut():
    assert len(parse_text("x" * 5000).text) <= 200


ROSTER = [RosterEntry("1", "Saquon Barkley"), RosterEntry("2", "DeVonta Smith"),
          RosterEntry("3", "Jalen Hurts"), RosterEntry("4", "Tanner Smith"),
          RosterEntry("5", "Amon-Ra St. Brown")]


def test_a_surname_alone_is_clear_when_only_one_player_has_it():
    ranked = rank_players("barkley", ROSTER)
    assert ranked[0].entry.espn_athlete_id == "1" and is_confident(ranked)


def test_a_shared_surname_is_not_clear():
    ranked = rank_players("smith", ROSTER)
    assert {c.entry.espn_athlete_id for c in ranked[:2]} == {"2", "4"}
    assert not is_confident(ranked)


def test_full_name_and_typo():
    assert is_confident(rank_players("devonta smith", ROSTER))
    ranked = rank_players("barkly", ROSTER)
    assert ranked and ranked[0].entry.espn_athlete_id == "1"


def test_nobody_matches_nonsense():
    assert rank_players("zzzz qqqq", ROSTER) == []


@pytest.mark.parametrize(("name", "expected"), [
    ("J.Hurts", "3"), ("S.Barkley", "1"), ("D.Smith", "2"), ("T.Smith", "4"),
    ("A.St. Brown", "5"), ("X.Smith", None), ("Smith", None),
])
def test_match_abbreviated(name, expected):
    assert match_abbreviated(name, ROSTER) == expected


def test_parse_reply_is_forgiving():
    r = parse_reply('```json\n{"player_name": " Saquon Barkley ", "team": null, '
                    '"play": "touchdown", "yards": "60"}\n```')
    assert (r.player_name, r.play, r.yards) == ("Saquon Barkley", P.TOUCHDOWN, 60)
    assert parse_reply('{"player_name": 5, "play": "dunk", "yards": "far"}') == \
        parse_reply('{"player_name": null, "play": null, "yards": null}')
    assert parse_reply("not json") is None


def test_llm_reader_sends_one_strict_request_and_survives_failure():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '{"player_name": "Saquon Barkley", "team": "PHI", "play": "run", "yards": 60}'}}]})

    reader = LlmReader("sk-test", "https://openrouter.ai/api/v1", "some/model", Budget(1),
                       httpx.Client(transport=httpx.MockTransport(handler)))
    reading = reader.read("sakwon 60 yd run")
    assert reading.player_name == "Saquon Barkley" and reading.play is P.RUN
    body = seen[0].read().decode()
    assert '"temperature":0' in body.replace(" ", "") and "json_schema" in body
    assert seen[0].headers["authorization"] == "Bearer sk-test"
    assert reader.read("again") is None  # over the daily budget: no call
    assert len(seen) == 1

    def broken(request):
        return httpx.Response(503)

    reader = LlmReader("sk-test", "https://x.test/v1", "m", Budget(5),
                       httpx.Client(transport=httpx.MockTransport(broken)))
    assert reader.read("anything") is None
