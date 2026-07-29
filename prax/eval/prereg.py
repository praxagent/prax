"""Pre-registered kill conditions for eval-gate experiments.

Before a gate run, write down what result would REFUTE the change being tested.
After the run, the verdict is checked against that pre-registration — not
against a judgement formed while looking at the numbers.

Why this exists (docs/research/ilands-grounding-gap.md): an authored evaluation
degrades under optimization pressure, and the cheapest place that happens is not
in the metric but in the *interpretation* — a disappointing result quietly
becomes "inconclusive, keep the flag" when nobody wrote down beforehand that it
would count as disproof. The flag-eval campaign already rejects flags on
evidence; this makes the standard of evidence something committed in advance,
one paragraph per experiment.

A pre-registration is deliberately tiny:

    prereg = register(
        experiment="verify_discipline_v2",
        hypothesis="verify-discipline raises private avg without raising cost",
        kill_condition="private avg does not improve by >= 0.02, or cost ratio > 1.05",
        kill=lambda r: r["private_delta"] < 0.02 or r["cost_ratio"] > 1.05,
    )
    ...run...
    verdict = prereg.judge({"private_delta": 0.01, "cost_ratio": 1.0})
    # -> verdict.killed is True, and the record says WHY in the words written
    #    before anyone saw 0.01.

The registration is persisted BEFORE the run (a JSON line with a timestamp), so
"we would have accepted X as disproof" is checkable after the fact.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_LOG = "eval_preregistrations.jsonl"


def _log_path() -> Path:
    root = os.environ.get("PRAX_EVAL_DIR", "") or "."
    return Path(root) / _DEFAULT_LOG


@dataclass
class Verdict:
    experiment: str
    killed: bool
    kill_condition: str
    observed: dict[str, Any]

    def summary(self) -> str:
        state = "KILLED" if self.killed else "survived"
        return (f"[{state}] {self.experiment} — pre-registered kill condition: "
                f"{self.kill_condition}")


@dataclass
class PreRegistration:
    experiment: str
    hypothesis: str
    kill_condition: str
    kill: Callable[[dict[str, Any]], bool]
    registered_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat())

    def judge(self, observed: dict[str, Any]) -> Verdict:
        """Apply the pre-registered condition to observed results.

        The lambda decides; the string is what humans audit. If they disagree,
        that is a bug in the experiment worth knowing about, which is why both
        are recorded.
        """
        killed = bool(self.kill(observed))
        _append({
            "event": "judged",
            "experiment": self.experiment,
            "killed": killed,
            "observed": observed,
            "kill_condition": self.kill_condition,
            "judged_at": datetime.now(UTC).isoformat(),
        })
        return Verdict(self.experiment, killed, self.kill_condition, observed)


def register(*, experiment: str, hypothesis: str, kill_condition: str,
             kill: Callable[[dict[str, Any]], bool]) -> PreRegistration:
    """Record the refutation standard BEFORE the run.

    Refuses an empty kill condition: "nothing would change my mind" is not an
    experiment, it is a decision wearing one's clothes.
    """
    if not kill_condition.strip():
        raise ValueError(
            "A pre-registration needs a non-empty kill condition — if no result "
            "would refute the hypothesis, this is not an experiment.")
    reg = PreRegistration(experiment=experiment, hypothesis=hypothesis,
                          kill_condition=kill_condition, kill=kill)
    _append({
        "event": "registered",
        "experiment": experiment,
        "hypothesis": hypothesis,
        "kill_condition": kill_condition,
        "registered_at": reg.registered_at,
    })
    return reg


def _append(entry: dict[str, Any]) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        # The log is an audit convenience; losing a line must not break a run.
        # The in-memory registration still governs the verdict.
        pass


def history(experiment: str | None = None) -> list[dict[str, Any]]:
    """Read back past registrations/verdicts (newest last)."""
    path = _log_path()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if experiment is None or entry.get("experiment") == experiment:
            out.append(entry)
    return out
