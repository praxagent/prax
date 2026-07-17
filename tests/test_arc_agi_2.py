"""Key-free tests for the ARC-AGI-2 adapter (grid parsing + exact-match pass@2)."""
from __future__ import annotations

from prax.eval.benchmarks import get_adapter
from prax.eval.benchmarks.arc_agi_2 import _parse_answer_grids, score


def test_seed_case_shape():
    a = get_adapter("arc_agi_2")
    cases = a.cases()
    assert cases and "train" in cases[0] and "test_output" in cases[0]
    p = a.prompt(cases[0])
    assert "ANSWER:" in p and "Test input" in p


def test_score_exact_match_and_pass_at_2():
    case = {"test_output": [[8, 0, 7], [0, 6, 0]]}
    # Correct grid after an ANSWER: marker → pass.
    assert score(case, "reasoning...\nANSWER: [[8,0,7],[0,6,0]]")["passed"] is True
    # Wrong single guess → fail.
    assert score(case, "ANSWER: [[1,2,3],[4,5,6]]")["passed"] is False
    # Second attempt correct → pass@2.
    r = "ANSWER: [[1,1,1],[0,6,0]]\nANSWER2: [[8,0,7],[0,6,0]]"
    assert score(case, r)["passed"] is True


def test_parse_handles_pretty_printed_and_unmarked():
    # Pretty-printed multi-line grid after marker.
    grids = _parse_answer_grids("ANSWER:\n[[1, 2],\n [3, 4]]")
    assert grids == [[[1, 2], [3, 4]]]
    # No marker → fall back to the last valid 2D int array in the text.
    grids = _parse_answer_grids("junk [[9]] more text [[1,2],[3,4]] tail")
    assert grids[-1] == [[1, 2], [3, 4]]


def test_score_rejects_ragged_or_nongrid():
    case = {"test_output": [[1, 2]]}
    assert score(case, "ANSWER: [[1,2,3],[4]]")["passed"] is False  # ragged → not a grid
    assert score(case, "ANSWER: not a grid")["passed"] is False
