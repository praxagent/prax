"""Score self-reported confidence against what actually happened.

Prax's completion tools now ask for a probability ("how sure are you that an
independent grader would call this done?"). **An unscored confidence field is
decoration** — it only becomes evidence once it is graded, which is what this
module does.

The measurement that motivated it: on a full Terminal-Bench 2.0 sweep, 55 of
65 scored failures (85%) declared success and were overruled by the hidden
verifier. That is a *calibration* failure — the agent did not know what it did
not know. Resolution here comes from an external verdict (a hidden verifier, a
golden's deterministic scorer), never from a rubric Prax wrote, so the score is
grounded and cannot be improved by rewriting the rubric.

Two numbers, both standard:

- **Brier score** — mean squared error of the probabilities. 0 is perfect,
  0.25 is what you get by always saying 0.5, and >0.25 means the forecasts are
  worse than a shrug.
- **Overconfidence** — mean(confidence) − observed base rate. Positive means
  claiming more certainty than the outcomes justify, which is the failure mode
  actually observed.

Both are undefined on an empty set and say so (``None``), rather than
returning a flattering 0.0 — same rule as unknown-cost never rendering as
$0.00.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["CalibrationReport", "score_calibration", "score_harbor_job"]


@dataclass
class CalibrationReport:
    """Calibration over a set of (confidence, outcome) pairs."""

    n: int
    brier: float | None
    mean_confidence: float | None
    base_rate: float | None
    overconfidence: float | None
    # (bin_label, count, mean_confidence, observed_rate) per decile with data.
    bins: list[tuple[str, int, float, float]] = field(default_factory=list)

    def summary(self) -> str:
        if not self.n:
            return "calibration: no resolved forecasts"
        return (
            f"calibration: n={self.n} brier={self.brier:.3f} "
            f"claimed={self.mean_confidence:.2f} actual={self.base_rate:.2f} "
            f"overconfidence={self.overconfidence:+.2f}"
        )


def score_calibration(pairs) -> CalibrationReport:
    """Score ``(confidence, outcome)`` pairs.

    *confidence* is a probability in [0, 1]; *outcome* is truthy if the claim
    was borne out by the external verdict. Pairs whose confidence is missing
    or unparseable are dropped — a claim with no stated probability is not a
    forecast, and silently scoring it as 0.5 would invent data.
    """
    clean: list[tuple[float, int]] = []
    for conf, outcome in pairs:
        if conf is None:
            continue
        try:
            c = float(conf)
        except (TypeError, ValueError):
            continue
        if not 0.0 <= c <= 1.0:
            # Out-of-range is a bug in the caller, not a forecast to grade.
            continue
        clean.append((c, 1 if outcome else 0))

    n = len(clean)
    if not n:
        return CalibrationReport(n=0, brier=None, mean_confidence=None,
                                 base_rate=None, overconfidence=None)

    brier = sum((c - o) ** 2 for c, o in clean) / n
    mean_conf = sum(c for c, _ in clean) / n
    base = sum(o for _, o in clean) / n

    bins: list[tuple[str, int, float, float]] = []
    for lo in range(0, 10):
        low, high = lo / 10, (lo + 1) / 10
        # Last bin is closed so a confidence of exactly 1.0 is counted.
        group = [(c, o) for c, o in clean
                 if (low <= c < high or (lo == 9 and c == 1.0))]
        if group:
            bins.append((
                f"{low:.1f}-{high:.1f}",
                len(group),
                round(sum(c for c, _ in group) / len(group), 3),
                round(sum(o for _, o in group) / len(group), 3),
            ))

    return CalibrationReport(
        n=n,
        brier=round(brier, 4),
        mean_confidence=round(mean_conf, 4),
        base_rate=round(base, 4),
        overconfidence=round(mean_conf - base, 4),
        bins=bins,
    )


def score_harbor_job(job_dir) -> CalibrationReport:
    """Grade the confidences recorded by a Terminal-Bench (harbor) job.

    Reads each trial's ``agent_result.metadata.confidence`` and resolves it
    against the task's own hidden verifier — an external verdict, never a
    rubric of ours. Trials that never scored (infrastructure timeouts) are
    skipped: an unresolved forecast is not a wrong one.
    """
    import json
    from pathlib import Path

    pairs = []
    for f in Path(job_dir).glob("*/*__*/result.json"):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        reward = ((d.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if reward is None:
            continue  # never scored → nothing to resolve against
        conf = ((d.get("agent_result") or {}).get("metadata") or {}).get("confidence")
        pairs.append((conf, reward == 1.0))
    return score_calibration(pairs)
