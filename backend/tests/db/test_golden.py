"""The lookup golden set through the deterministic path (no LLM), on the recorded game in play.

Never a confident wrong answer: that would show someone the wrong bet. The other numbers are
floors: raise them when the parser gets better, never lower them to make a change pass.
scripts/eval_lookup.py runs the same set with the LLM fallback, to choose LLM_MODEL.
"""
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from juju.api.lookup import resolve
from juju.dev_seed import seed_phi_chi_scenario
from juju.ingest import espn
from tests.lookups import Report, Verdict, load_golden
from tests.support import FIXTURES

pytestmark = pytest.mark.db

EXACT_AT_LEAST = 125
MISSED_AT_MOST = 3
FIELDS_RIGHT_AT_LEAST = 206


def test_the_golden_set_is_big_enough_and_names_real_players():
    golden = load_golden()
    assert len(golden) >= 150
    assert len({g.text for g in golden}) == len(golden)
    rostered = {f"player:{p.espn_athlete_id}" for team in ("21", "3") for p in espn.parse_roster(
        json.loads((FIXTURES / "espn" / f"nfl_roster_{team}.json").read_text()))}
    for g in golden:
        for want in g.want:
            assert want in rostered or want in ("team:21", "team:3"), (g.text, want)


def test_the_deterministic_path_on_the_golden_set(db):
    now = datetime.now(tz=UTC)
    report = Report()
    with Session(db) as s:
        seed_phi_chi_scenario(s, "live", now)
        for g in load_golden():
            report.add(g, resolve(s, g.text, now))
    print(report.summary())
    assert report.wrong == [], "confidently wrong:\n" + "\n".join(report.wrong)
    assert report.verdicts[Verdict.EXACT] >= EXACT_AT_LEAST, report.summary()
    assert report.verdicts[Verdict.MISSED] <= MISSED_AT_MOST, "\n".join(report.missed)
    assert report.fields_right >= FIELDS_RIGHT_AT_LEAST, "\n".join(report.field_errors)


def _eval_script():
    import importlib.util
    path = FIXTURES.parents[1] / "scripts" / "eval_lookup.py"
    spec = importlib.util.spec_from_file_location("eval_lookup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeModel:
    """Stands in for a model: expands a few first names, answers nothing else."""
    NAMES = {"Saquon TD": "Saquon Barkley", "Rome catch": "Rome Odunze",
             "Jalen TD run": "Jalen Hurts"}

    def read(self, text):
        from juju.ingest.llm import Reading
        name = self.NAMES.get(text)
        return Reading(name, None, None, None) if name else None


def test_the_eval_script_scores_the_llm_path(db):
    script = _eval_script()
    now = datetime.now(tz=UTC)
    reader = script.TimedReader(FakeModel())
    with Session(db) as s:
        seed_phi_chi_scenario(s, "live", now)
        base = script.evaluate(s, now)
        report = script.evaluate(s, now, reader)
    unsure = base.by_path["choices"] + base.by_path["none"]
    assert reader.calls == unsure and reader.failed == unsure - len(FakeModel.NAMES)
    assert report.by_path["llm"] == len(FakeModel.NAMES) and report.wrong == []
    assert report.verdicts[Verdict.EXACT] == base.verdicts[Verdict.EXACT] + 3
    assert script._percentile([10.0, 20.0, 30.0], 50) == 20.0
