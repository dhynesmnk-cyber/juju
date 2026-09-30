"""Score the lookup golden set, with or without the LLM fallback, to choose LLM_MODEL.

    python scripts/eval_lookup.py                          # the deterministic path only
    python scripts/eval_lookup.py --model MODEL [--model MODEL ...] [--yes]

It seeds the recorded PHI @ CHI game (in play) into DATABASE_URL, whose database name must
contain "dev" or "test", then runs every line of tests/fixtures/lookups/golden.jsonl through
`resolve`, the same code the site uses. With --model it uses LLM_API_KEY and LLM_BASE_URL from
the environment (OpenRouter by default) and calls the model only where the site would: when the
deterministic reading isn't sure. It says how many calls that is and asks before making them.

Pick the model with no "wrong" answers, then the most "exact", then the lowest p95 latency
(the site gives the model 3 seconds). "wrong" means a confident wrong player: never acceptable.
"""
import argparse
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from juju.api.lookup import resolve  # noqa: E402
from juju.config import get_settings  # noqa: E402
from juju.db import make_engine  # noqa: E402
from juju.dev_seed import seed_phi_chi_scenario  # noqa: E402
from juju.ingest.llm import Budget, LlmReader, Reading  # noqa: E402
from tests.lookups import Report, load_golden  # noqa: E402


class TimedReader:
    """Wraps a reader: counts calls, failures (no reading) and how long each took."""

    def __init__(self, reader):
        self._reader = reader
        self.calls = 0
        self.failed = 0
        self.ms: list[float] = []

    def read(self, text: str) -> Reading | None:
        start = time.perf_counter()
        reading = self._reader.read(text)
        self.ms.append((time.perf_counter() - start) * 1000)
        self.calls += 1
        self.failed += reading is None
        return reading


def evaluate(session: Session, now: datetime, llm=None) -> Report:
    report = Report()
    for g in load_golden():
        report.add(g, resolve(session, g.text, now, llm))
    return report


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(p) - 1]


def _print(title: str, report: Report, show_lists: bool) -> None:
    print(f"\n{title}\n  {report.summary()}")
    if show_lists:
        for name in ("wrong", "missed", "field_errors"):
            for line in getattr(report, name):
                print(f"  {name}: {line}")


def main(argv: list[str]) -> int:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--model", action="append", default=[], help="an LLM model id; repeat")
    args.add_argument("--yes", action="store_true", help="don't ask before calling the LLM")
    args.add_argument("--quiet", action="store_true", help="summaries only")
    opts = args.parse_args(argv)

    settings = get_settings()
    database = make_url(settings.database_url).database or ""
    if "dev" not in database and "test" not in database:
        print(f"refusing to seed {database!r}: use a database whose name has 'dev' or 'test'")
        return 2
    engine = make_engine()
    now = datetime.now(tz=UTC)
    with Session(engine) as session:
        seed_phi_chi_scenario(session, "live", now)
        base = evaluate(session, now)
        _print("Deterministic only", base, not opts.quiet)
        if not opts.model:
            return 0
        if settings.llm_api_key is None:
            print("\nLLM_API_KEY is not set")
            return 2
        unsure = base.by_path["choices"] + base.by_path["none"]
        print(f"\nEach model is called at most for the {unsure} lookups the deterministic path "
              f"isn't sure about: up to {unsure * len(opts.model)} calls in all.")
        if not opts.yes and input("Make these calls? [y/N] ").strip().lower() != "y":
            return 1
        rows = []
        for model in opts.model:
            reader = TimedReader(LlmReader(settings.llm_api_key.get_secret_value(),
                                           settings.llm_base_url, model, Budget(100_000)))
            report = evaluate(session, now, reader)
            _print(f"With {model}", report, not opts.quiet)
            rows.append((model, report, reader))
    print(f"\n{'model':40} {'exact':>5} {'asked':>5} {'missed':>6} {'wrong':>5} "
          f"{'calls':>5} {'failed':>6} {'p50 ms':>7} {'p95 ms':>7}")
    for model, report, reader in rows:
        v = report.verdicts
        print(f"{model[:40]:40} {v['exact']:>5} {v['asked']:>5} {v['missed']:>6} "
              f"{v['wrong']:>5} {reader.calls:>5} {reader.failed:>6} "
              f"{_percentile(reader.ms, 50):>7.0f} {_percentile(reader.ms, 95):>7.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
