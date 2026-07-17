"""Tests for eval statistical honesty (Wilson CIs + aggregate attachment)."""
from __future__ import annotations

from prax.eval.stats import attach_ci, wilson_ci


def test_wilson_behaves_at_extremes():
    # 40/40 must NOT be [1.0, 1.0] — the Wilson lower bound is well under 1.
    low, high = wilson_ci(40, 40)
    assert high == 1.0
    assert 0.90 < low < 0.92          # ~91.2%
    # A middling rate straddles sensibly (Wilson 32/40 → ~[0.652, 0.895]).
    low, high = wilson_ci(32, 40)     # 80%
    assert 0.64 < low < 0.67 and 0.88 < high < 0.91
    # No data → no claim.
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_wilson_symmetric_and_bounded():
    lo, hi = wilson_ci(0, 20)
    assert lo == 0.0 and 0.0 < hi < 0.20     # bounded at 0
    lo, hi = wilson_ci(20, 20)
    assert hi == 1.0 and 0.80 < lo < 1.0     # bounded at 1


def test_attach_ci_adds_fields():
    agg = {"benchmark": "x", "graded": 40, "passed": 32, "pass_rate": 0.8}
    attach_ci(agg)
    assert agg["n"] == 40
    assert len(agg["pass_rate_ci95"]) == 2
    assert "95% CI" in agg["pass_rate_str"] and "n=40" in agg["pass_rate_str"]


def test_attach_ci_noop_without_counts():
    agg = {"benchmark": "x"}          # no passed/graded
    attach_ci(agg)
    assert "pass_rate_ci95" not in agg   # safe no-op
