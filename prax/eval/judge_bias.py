"""Bias probes for Prax's LLM-judge surfaces.

The stability half of the judge audit (task #36) pinned ``JUDGE_TEMPERATURE`` at
the four grader sites, because every one of them had been inheriting
``AGENT_TEMPERATURE`` (0.7) — the knob tuned to make the *agent* write well. That
fixed a *variance* problem. It said nothing about *bias*: a judge can be
perfectly reproducible and reproducibly wrong.

This module measures the four biases a rubric judge is known to exhibit. Each
probe is a function of an injectable ``judge`` callable, so the whole module is
exercisable with **no API key** (that is how the CI tests drive it) and paid only
when run against a real model.

The four:

``probe_length_bias``
    Does the judge reward *length* rather than *substance*? Grade the same answer
    twice — once as written, once padded with information-free filler.

``probe_verdict_drift``
    Pinning temperature is **necessary, not sufficient**: providers are not
    bit-deterministic, and a MoE router can put the same prompt on a different
    expert. Re-grade one answer N times and count non-unanimous criteria.

``probe_criterion_order``
    The classic position-bias probe compares two *candidates* in fixed order.
    Prax has **no pairwise judge surface** — nothing anywhere ranks A against B —
    so that probe has nothing to attach to. What Prax does have is an *ordered
    list of rubric criteria* in one prompt. Permute the order, re-grade, and see
    whether a criterion's verdict depends on where it sat.

``probe_self_preference``
    The judge tier defaults to the same ladder the agent under test runs on, so
    a model can end up grading its own family's output. This is the only probe
    that needs a 2x2: answers from >=2 generator families, graded by >=2 judge
    families. A single judge disagreeing with another is NOT self-preference —
    it is disagreement. Self-preference is the **interaction**: does a judge rate
    its own family higher than the other judges rate that same answer?

Every probe returns a :class:`ProbeResult` carrying the effect size, the n it was
computed over, and enough detail to reconstruct it. None of them return a bare
number: an effect size without an n is exactly the presentation defect the
MATRIX.md sampling audit caught.
"""
from __future__ import annotations

import itertools
import logging
import random
from dataclasses import dataclass, field

from prax.eval.goldens import Golden, score_golden

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    """One bias measurement.

    ``effect`` is signed and probe-specific (see each probe's docstring for its
    units). ``threshold`` is the pre-registered line above which the effect is
    reported as present — chosen per probe, not per result, so a run cannot move
    its own goalposts.
    """

    probe: str
    n: int
    effect: float
    threshold: float
    detail: dict = field(default_factory=dict)
    void: str = ""  # non-empty => the probe's precondition failed; effect is meaningless
    low_power: str = ""  # non-empty => the effect is real but the test could barely detect one

    @property
    def flagged(self) -> bool:
        """True when the measured effect exceeds the pre-registered threshold.

        A void probe is never flagged AND never clean — read ``void`` first.
        """
        return not self.void and abs(self.effect) > self.threshold

    def as_record(self) -> dict:
        """Serializable form INCLUDING the derived fields.

        ``dataclasses.asdict`` silently drops ``flagged`` and ``summary`` because
        they are computed, not stored — so a record built from it looks complete
        and is missing the verdict. Persist through this instead.
        """
        from dataclasses import asdict
        return {**asdict(self), "flagged": self.flagged, "summary": self.summary()}

    def summary(self) -> str:
        if self.void:
            return f"{self.probe}: VOID — {self.void}"
        verdict = "BIAS DETECTED" if self.flagged else "within threshold"
        if self.low_power and not self.flagged:
            verdict = f"no effect seen, but LOW POWER — {self.low_power}"
        return (f"{self.probe}: effect={self.effect:+.3f} "
                f"(threshold {self.threshold:.3f}, n={self.n}) — {verdict}")


# INVARIANT: a null result is only evidence of absence when the test could have
# detected a presence. A judge that scores every criterion 0 (or every criterion
# 1) is at the END of the scale: it is never near a decision boundary, which is
# exactly where bias lives, so "no drift, no order effect" measured there says
# almost nothing about the judge. Every probe therefore reports baseline
# saturation alongside its effect, and a clean result at a saturated baseline is
# rendered as LOW POWER rather than as "within threshold".
#
# Found by reading the first real run: the answer scored 0/5 on every criterion
# and all three probes returned exactly +0.000. Publishing that as "no bias
# detected" would have been the same presentation defect as a pass rate that
# sheds its caveats on the way to the reader.
def _saturation_note(total: float, *, what: str = "baseline") -> str:
    if total <= 0.0:
        return f"{what} scored 0.0 — floor of the scale, no room for a downward effect"
    if total >= 1.0:
        return f"{what} scored 1.0 — ceiling of the scale, no room for an upward effect"
    return ""


# ---------------------------------------------------------------------------
# Length bias
# ---------------------------------------------------------------------------

# Information-free filler. Every sentence is a discourse connective with no
# factual content, no named entity, and no number — so it cannot satisfy any
# rubric criterion. That property is what makes the probe a *length* probe.
_FILLER = [
    "It is worth taking a moment to consider this point carefully.",
    "This is, on reflection, an aspect that merits further attention.",
    "There are, of course, several dimensions along which this can be viewed.",
    "Taken as a whole, the considerations above deserve to be weighed together.",
    "It should be emphasised that the foregoing is central to the overall picture.",
    "Broadly speaking, the matter can be approached from more than one direction.",
]


def pad_with_filler(text: str, *, factor: int = 3, seed: int = 0) -> str:
    """Interleave information-free filler until *text* is roughly *factor* x longer.

    INVARIANT: the padding adds LENGTH and NOTHING ELSE. If a padded answer ever
    scores higher on a criterion, the only thing that changed is how long it is.
    The filler carries no entity, number, citation or claim, and
    :func:`probe_length_bias` additionally re-checks every deterministic
    ``verify`` regex on both variants and voids itself if any of them moved.
    """
    rng = random.Random(seed)
    paras = [p for p in text.split("\n") if p.strip()] or [text]
    target = len(text) * max(1, factor)
    out: list[str] = []
    i = 0
    while len("\n".join(out)) < target and i < 500:
        out.append(paras[i % len(paras)] if i % 2 == 0 else rng.choice(_FILLER))
        i += 1
        if i >= len(paras) * 2 and len("\n".join(out)) >= target:
            break
    # Always keep the original answer intact and first, so substance is unchanged.
    return text + "\n" + "\n".join(rng.choice(_FILLER) for _ in range(max(1, i)))


def probe_length_bias(golden: Golden, answer: str, *, judge=None,
                      pad=pad_with_filler, threshold: float = 0.05,
                      tier: str = "low") -> ProbeResult:
    """Grade *answer* as written and padded with filler.

    ``effect`` = padded total - terse total, in rubric-score units (0..1). A
    positive effect means the judge paid for length it did not receive substance
    for.

    The probe VOIDS itself if padding changed any deterministic ``verify``
    criterion, because that would mean the filler added gradeable content and the
    comparison is no longer about length.
    """
    terse = score_golden(golden, answer, judge=judge, tier=tier)
    padded_text = pad(answer)
    padded = score_golden(golden, padded_text, judge=judge, tier=tier)

    verified_keys = [c.key for c in golden.rubric if c.verify]
    moved = [k for k in verified_keys
             if terse.get("scores", {}).get(k) != padded.get("scores", {}).get(k)]
    void = ""
    if moved:
        void = f"padding changed deterministic criteria {moved} — filler added gradeable content"

    # A terse baseline of 1.0 leaves nowhere for padding to gain; a baseline of
    # 0.0 still permits the upward effect the hypothesis predicts, so only the
    # ceiling costs this probe its power.
    return ProbeResult(
        probe="length_bias",
        n=1,
        effect=round(padded.get("total", 0.0) - terse.get("total", 0.0), 4),
        threshold=threshold,
        void=void,
        low_power=("terse baseline scored 1.0 — ceiling, padding cannot gain"
                   if terse.get("total", 0.0) >= 1.0 else ""),
        detail={
            "golden": golden.id,
            "terse_total": terse.get("total"),
            "padded_total": padded.get("total"),
            "terse_chars": len(answer),
            "padded_chars": len(padded_text),
            "terse_scores": terse.get("scores"),
            "padded_scores": padded.get("scores"),
        },
    )


# ---------------------------------------------------------------------------
# Verdict drift
# ---------------------------------------------------------------------------

def probe_verdict_drift(golden: Golden, answer: str, *, judge=None, trials: int = 5,
                        threshold: float = 0.0, tier: str = "low") -> ProbeResult:
    """Re-grade one (golden, answer) *trials* times and count non-unanimous criteria.

    ``effect`` = fraction of judged criteria whose verdict was not identical on
    every trial. ``threshold`` defaults to **0.0**: with temperature pinned, ANY
    drift is a finding, because a published eval number is supposed to be
    reproducible.

    INVARIANT: only criteria WITHOUT a ``verify`` regex are counted. Deterministic
    criteria cannot drift by construction, and including them would dilute the
    denominator and make a drifting judge look stabler than it is.
    """
    judged_keys = [c.key for c in golden.rubric if not c.verify]
    runs = [score_golden(golden, answer, judge=judge, tier=tier) for _ in range(max(2, trials))]
    per_key: dict[str, list] = {k: [] for k in judged_keys}
    for r in runs:
        for k in judged_keys:
            per_key[k].append(r.get("scores", {}).get(k))
    unstable = [k for k, vals in per_key.items() if len(set(vals)) > 1]
    totals = [r.get("total", 0.0) for r in runs]

    return ProbeResult(
        probe="verdict_drift",
        n=len(runs),
        effect=round(len(unstable) / len(judged_keys), 4) if judged_keys else 0.0,
        threshold=threshold,
        low_power=_saturation_note(totals[0] if totals else 0.0),
        detail={
            "golden": golden.id,
            "unstable_criteria": unstable,
            "judged_criteria": judged_keys,
            "totals": totals,
            "total_spread": round(max(totals) - min(totals), 4) if totals else 0.0,
            "per_key": per_key,
        },
    )


# ---------------------------------------------------------------------------
# Criterion-order sensitivity (the position-bias analogue that has a surface)
# ---------------------------------------------------------------------------

def probe_criterion_order(golden: Golden, answer: str, *, judge=None,
                          orders: int = 3, threshold: float = 0.0,
                          tier: str = "low", seed: int = 0) -> ProbeResult:
    """Re-grade with the rubric criteria PERMUTED and see whether verdicts move.

    ``effect`` = fraction of judged criteria whose verdict depends on its position
    in the prompt. Threshold 0.0 — a criterion's verdict must be a function of the
    criterion and the answer, never of where it sat in a list.

    INVARIANT: the criterion SET is identical across permutations; only the order
    differs. Weights and keys travel with each criterion, so the reported ``total``
    stays comparable — ``score_golden`` weights by ``golden.rubric``, which is why
    a permuted copy of the Golden is passed rather than a permuted prompt.
    """
    from dataclasses import replace

    judged_keys = [c.key for c in golden.rubric if not c.verify]
    if len(golden.rubric) < 2:
        return ProbeResult(probe="criterion_order", n=0, effect=0.0, threshold=threshold,
                           void="rubric has fewer than 2 criteria — nothing to permute")

    rng = random.Random(seed)
    perms: list[list] = [list(golden.rubric)]
    seen = {tuple(c.key for c in golden.rubric)}
    # Deterministic enumeration for small rubrics, sampled shuffles for large ones.
    if len(golden.rubric) <= 5:
        for p in itertools.permutations(golden.rubric):
            if len(perms) >= orders:
                break
            if tuple(c.key for c in p) not in seen:
                seen.add(tuple(c.key for c in p))
                perms.append(list(p))
    else:
        while len(perms) < orders:
            p = list(golden.rubric)
            rng.shuffle(p)
            if tuple(c.key for c in p) not in seen:
                seen.add(tuple(c.key for c in p))
                perms.append(p)

    per_key: dict[str, list] = {k: [] for k in judged_keys}
    positions: dict[str, list] = {k: [] for k in judged_keys}
    totals: list[float] = []
    for p in perms:
        r = score_golden(replace(golden, rubric=p), answer, judge=judge, tier=tier)
        totals.append(float(r.get("total", 0.0)))
        for idx, c in enumerate(p):
            if c.key in per_key:
                per_key[c.key].append(r.get("scores", {}).get(c.key))
                positions[c.key].append(idx)
    unstable = [k for k, vals in per_key.items() if len(set(vals)) > 1]

    return ProbeResult(
        probe="criterion_order",
        n=len(perms),
        effect=round(len(unstable) / len(judged_keys), 4) if judged_keys else 0.0,
        threshold=threshold,
        low_power=_saturation_note(totals[0] if totals else 0.0),
        detail={
            "golden": golden.id,
            "totals": totals,
            "orders": [[c.key for c in p] for p in perms],
            "unstable_criteria": unstable,
            "per_key": per_key,
            "positions": positions,
        },
    )


# ---------------------------------------------------------------------------
# Self-preference
# ---------------------------------------------------------------------------

def probe_self_preference(golden: Golden, answers: dict[str, str],
                          judges: dict[str, object], *, threshold: float = 0.05,
                          tier: str = "low") -> ProbeResult:
    """Measure whether a judge rates its OWN family's answer above other judges do.

    *answers* maps ``family -> answer text generated by that family``; *judges*
    maps ``family -> judge callable``. Both must share at least two family labels,
    because self-preference is an INTERACTION and cannot be computed from one
    judge's scores.

    ``effect`` = mean over shared families of
    ``score(judge_f, answer_f) - mean(score(judge_g, answer_f) for g != f)``,
    in rubric-score units. Positive = self-preference.

    INVARIANT: every judge grades EVERY answer. A missing cell would let the
    average be taken over a different answer set per judge, which is the same
    incomparable-denominator defect as scoring an eval on the cases that returned.
    """
    families = sorted(set(answers) & set(judges))
    if len(families) < 2:
        return ProbeResult(probe="self_preference", n=0, effect=0.0, threshold=threshold,
                           void=f"need >=2 families present in both answers and judges; got {families}")

    grid: dict[str, dict[str, float]] = {}
    for jf in families:
        grid[jf] = {}
        for af in families:  # full grid — every judge grades every answer
            r = score_golden(golden, answers[af], judge=judges[jf], tier=tier)
            grid[jf][af] = float(r.get("total", 0.0))

    deltas = {}
    for f in families:
        own = grid[f][f]
        others = [grid[g][f] for g in families if g != f]
        deltas[f] = round(own - (sum(others) / len(others)), 4)

    flat = [v for row in grid.values() for v in row.values()]
    return ProbeResult(
        probe="self_preference",
        n=len(families) ** 2,
        effect=round(sum(deltas.values()) / len(deltas), 4),
        threshold=threshold,
        low_power=(_saturation_note(flat[0], what="every cell in the grid")
                   if len(set(flat)) == 1 else ""),
        detail={
            "golden": golden.id,
            "families": families,
            "grid": grid,          # grid[judge_family][answer_family]
            "per_family_delta": deltas,
        },
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_bias_audit(golden: Golden, answer: str, *, judge=None, tier: str = "low",
                   trials: int = 5, orders: int = 3,
                   answers: dict[str, str] | None = None,
                   judges: dict[str, object] | None = None) -> dict:
    """Run every applicable probe and return ``{probe: ProbeResult}`` plus a summary.

    The self-preference probe runs only when a 2x2 is supplied; it is reported as
    ``skipped`` otherwise rather than silently omitted, so a reader can tell the
    difference between "no bias" and "not measured".
    """
    results: dict[str, ProbeResult] = {
        "length_bias": probe_length_bias(golden, answer, judge=judge, tier=tier),
        "verdict_drift": probe_verdict_drift(golden, answer, judge=judge, trials=trials, tier=tier),
        "criterion_order": probe_criterion_order(golden, answer, judge=judge, orders=orders, tier=tier),
    }
    if answers and judges:
        results["self_preference"] = probe_self_preference(golden, answers, judges, tier=tier)

    return {
        "golden": golden.id,
        "results": results,
        "skipped": [] if (answers and judges) else ["self_preference (no generator x judge grid supplied)"],
        "flagged": sorted(k for k, r in results.items() if r.flagged),
        "void": sorted(k for k, r in results.items() if r.void),
        "low_power": sorted(k for k, r in results.items() if r.low_power and not r.flagged),
        "summary": [r.summary() for r in results.values()],
    }
