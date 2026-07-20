"""Keyless tests for process (trace) grading — prax.eval.trace_grade."""
from __future__ import annotations

from prax.eval.trace_grade import grade_run, grade_trace


def test_committed_detects_non_answers():
    from prax.eval.trace_grade import _is_committed
    assert _is_committed("Answer: B", "") is True
    assert _is_committed("", "") is False
    assert _is_committed("   ", "") is False
    assert _is_committed(None, "") is False
    assert _is_committed("real answer", "boom") is False           # error present
    assert _is_committed("Connect timeout, please try again later.", "") is False


def test_verification_axis_requires_expected_tool():
    rubric = {"expected_tools": ["run_python"], "token_budget": 15000, "ideal_max_tool_calls": 2}
    used = grade_trace({"answer": "x", "tools": ["run_python"], "tokens": 5000}, rubric)
    assert used["criteria"]["verification"]["score"] == 1.0
    hand = grade_trace({"answer": "x", "tools": [], "tokens": 5000}, rubric)
    assert hand["criteria"]["verification"]["score"] == 0.0


def test_verification_axis_absent_when_no_expected_tools():
    # Nothing tool-checkable -> verification axis doesn't apply, weights renormalise.
    g = grade_trace({"answer": "x", "tools": [], "tokens": 1000}, {})
    assert "verification" not in g["criteria"]
    assert set(g["criteria"]) == {"committed", "efficiency"}


def test_efficiency_penalises_overage():
    rubric = {"token_budget": 15000, "ideal_max_tool_calls": 2}
    lean = grade_trace({"answer": "x", "tools": ["t"], "tokens": 8000}, rubric)
    fat = grade_trace({"answer": "x", "tools": ["t"] * 6, "tokens": 124000}, rubric)
    assert lean["criteria"]["efficiency"]["score"] == 1.0
    assert fat["criteria"]["efficiency"]["score"] < 0.5   # 124k tok + 6 calls both over


def test_non_commitment_tanks_the_score():
    rubric = {"expected_tools": ["run_python"]}
    g = grade_trace({"answer": "", "tools": ["run_python"], "tokens": 13000}, rubric)
    assert g["criteria"]["committed"]["score"] == 0.0
    assert g["trace_score"] < 0.7   # committed carries the most weight


def test_ideal_run_scores_high():
    rubric = {"expected_tools": ["run_python"], "token_budget": 15000, "ideal_max_tool_calls": 3}
    g = grade_trace({"answer": "counterexample", "tools": ["run_python"], "tokens": 9000}, rubric)
    assert g["trace_score"] >= 0.95   # committed + verified + efficient


def test_two_jacobian_runs_rank_as_expected():
    # The real data: the by-hand run is efficient but doesn't verify; the computed
    # run verifies but is wildly inefficient. Neither is ideal; both beat a spiral.
    rubric = {"expected_tools": ["run_python"], "token_budget": 15000, "ideal_max_tool_calls": 3}
    by_hand = grade_trace({"answer": "counterexample (verified 1 pt)", "tools": [],
                           "tokens": 6000}, rubric)
    computed = grade_trace({"answer": "counterexample (computed)",
                            "tools": ["run_python"] * 6, "tokens": 124782}, rubric)
    # computed verified (weight 0.35) so it edges out the unverified by-hand run...
    assert computed["trace_score"] > by_hand["trace_score"]
    # ...but neither reaches the ideal — each is missing a different axis.
    assert computed["trace_score"] < 0.95 and by_hand["trace_score"] < 0.95
    assert by_hand["criteria"]["verification"]["score"] == 0.0
    assert computed["criteria"]["efficiency"]["score"] < 0.5


def test_grade_run_keeps_answer_and_trace_separate():
    out = grade_run(
        {"answer": "Answer: B", "tools": [], "tokens": 3000},
        answer_grade={"passed": False, "score": 0.0},
        trace_rubric={})
    assert out["answer_score"] == 0.0
    assert "trace_score" in out and "trace" in out and "answer" in out
