"""The lookup golden set (tests/fixtures/lookups/golden.jsonl) and how a lookup is scored.

Every line is text someone might type about the recorded PHI @ CHI game, with what they meant:
`want` lists the acceptable answers ("player:<ESPN id>" or "team:<ESPN id>"; several when the
text fairly names more than one, none when nobody in the game fits). The optional `play`,
`yards`, `threshold` and `market` are what the text says, for scoring the parse on its own.

Used by tests/db/test_golden.py (the deterministic path, on every run) and by
scripts/eval_lookup.py (the same, plus the LLM fallback, to choose LLM_MODEL).
"""
import enum
import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from juju.api.lookup import Result
from juju.core.enums import PlayKind
from juju.ingest.parse import Parsed

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "lookups" / "golden.jsonl"
FIELDS = ("play", "yards", "threshold", "market")


class Verdict(enum.StrEnum):
    EXACT = "exact"    # straight to the right result
    ASKED = "asked"    # candidates, and the right one among them (or nothing claimed)
    MISSED = "missed"  # nothing useful: no result, or candidates without the right one
    WRONG = "wrong"    # straight to the wrong result: the one outcome that must not happen


@dataclass(frozen=True)
class Golden:
    text: str
    want: frozenset[str]
    expected: dict[str, object]  # only the fields the line states
    tags: tuple[str, ...]


def load_golden(path: Path = GOLDEN) -> list[Golden]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        expected: dict[str, object] = {k: row[k] for k in FIELDS if k in row}
        if "play" in expected:
            expected["play"] = PlayKind(expected["play"])
        if "threshold" in expected:
            expected["threshold"] = Decimal(str(expected["threshold"]))
        out.append(Golden(row["text"], frozenset(row["want"]), expected, tuple(row["tags"])))
    return out


def verdict(g: Golden, r: Result) -> Verdict:
    if r.kind in ("player", "team"):
        return Verdict.EXACT if f"{r.kind}:{r.id}" in g.want else Verdict.WRONG
    if r.kind == "choices":
        offered = {f"{c.kind}:{c.id}" for c in r.choices}
        return Verdict.ASKED if not g.want or offered & g.want else Verdict.MISSED
    return Verdict.EXACT if not g.want else Verdict.MISSED


def field_misses(g: Golden, parsed: Parsed) -> list[str]:
    got = {"play": parsed.play, "yards": parsed.yards, "threshold": parsed.threshold,
           "market": parsed.market_key}
    return [f"{k}: want {v}, got {got[k]}" for k, v in g.expected.items() if got[k] != v]


@dataclass
class Report:
    verdicts: Counter = field(default_factory=Counter)
    by_path: Counter = field(default_factory=Counter)
    fields_right: int = 0
    fields_total: int = 0
    wrong: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    field_errors: list[str] = field(default_factory=list)

    def add(self, g: Golden, r: Result) -> Verdict:
        v = verdict(g, r)
        self.verdicts[v] += 1
        self.by_path[r.path] += 1
        got = f"{r.kind}:{r.id}" if r.id else r.kind
        if v is Verdict.WRONG:
            self.wrong.append(f"{g.text!r}: got {got}, want {sorted(g.want)}")
        elif v is Verdict.MISSED:
            self.missed.append(f"{g.text!r}: got {got}, want {sorted(g.want)}")
        misses = field_misses(g, r.parsed)
        self.fields_total += len(g.expected)
        self.fields_right += len(g.expected) - len(misses)
        self.field_errors += [f"{g.text!r}: {m}" for m in misses]
        return v

    def summary(self) -> str:
        n = sum(self.verdicts.values())
        parts = [f"{v.value} {self.verdicts[v]}" for v in Verdict]
        return (f"{n} lookups: " + ", ".join(parts) + f"; parse fields "
                f"{self.fields_right}/{self.fields_total}; paths {dict(self.by_path)}")
