"""MMLU-Pro — broad multitask knowledge, the harder 10-option successor to MMLU
(arXiv 2406.01574).

The canonical "does it actually know things across domains" benchmark experts
expect to see. Ten answer options (A–J) instead of MMLU's four, with distractors
rewritten to defeat shallow elimination. Grading is deterministic: extract the
chosen letter and compare — no LLM judge.

Inline keyless seed set here (hand-authored representative items across subjects,
answers verified); the full 12k-question test set layers on via a gated loader
from PRAX_EVAL_DIR (never committed — benchmark-contamination firewall).
"""
from __future__ import annotations

import re

# Each case: question + ordered choices + the correct letter. Representative
# seed items (NOT copied from the held-out set) so keyless CI exercises the
# adapter; the real MMLU-Pro test split is loaded from PRAX_EVAL_DIR when present.
SEED_CASES: list[dict] = [
    {"id": "mmlup_calc",
     "question": "What is the derivative of f(x) = x^3 with respect to x?",
     "choices": ["3x^2", "x^2", "3x", "x^4/4", "x^3", "6x", "3x^3", "2x", "9x^2", "x^2/3"],
     "answer": "A"},
    {"id": "mmlup_units",
     "question": "Which expression has units equivalent to the joule?",
     "choices": ["kg·m/s", "kg·m^2/s^2", "kg·m^2/s", "kg/s^2", "kg·m/s^2",
                 "kg·s^2/m^2", "N/m", "W·s^2", "Pa·m^2", "kg·m^3/s^2"],
     "answer": "B"},
    {"id": "mmlup_ph",
     "question": "At 25 °C, the pH of a neutral aqueous solution is:",
     "choices": ["0", "1", "5", "7", "10", "14", "3.5", "12", "8.2", "6.5"],
     "answer": "D"},
    {"id": "mmlup_atp",
     "question": "In a eukaryotic cell, the organelle that is the primary site of "
                 "ATP synthesis via oxidative phosphorylation is the:",
     "choices": ["Nucleus", "Ribosome", "Golgi apparatus", "Lysosome",
                 "Mitochondrion", "Endoplasmic reticulum", "Peroxisome",
                 "Cytoskeleton", "Vacuole", "Centriole"],
     "answer": "E"},
    {"id": "mmlup_econ",
     "question": "In a perfectly competitive market at long-run equilibrium, a "
                 "representative firm earns:",
     "choices": ["Positive economic profit", "Monopoly rents",
                 "Zero economic profit", "Negative economic profit",
                 "Profit equal to fixed cost", "Profit above the shutdown point only",
                 "Accounting loss", "Profit set by the regulator", "Rent equal to output",
                 "Profit proportional to market share"],
     "answer": "C"},
    {"id": "mmlup_binsearch",
     "question": "The worst-case time complexity of binary search on a sorted array "
                 "of n elements is:",
     "choices": ["O(n)", "O(n^2)", "O(1)", "O(log n)", "O(n log n)", "O(2^n)",
                 "O(sqrt(n))", "O(n!)", "O(log log n)", "O(n/2)"],
     "answer": "D"},
]

_LETTERS = "ABCDEFGHIJ"


def _extract_letter(response: str, n: int) -> str | None:
    """Pull the chosen option letter (A..) from a free-form response.

    Prefers an explicit 'answer: X' / '(X)' / 'the answer is X'; falls back to the
    last standalone capital letter in range. Case-insensitive on the cue words.
    """
    valid = _LETTERS[:n]
    text = response or ""
    patterns = [
        rf"answer\s*(?:is|:)?\s*\(?([{valid}])\)?\b",
        rf"\boption\s*\(?([{valid}])\)?\b",
        rf"\(([{valid}])\)",
        rf"\b([{valid}])\b\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).upper()
    # Last resort: last standalone in-range letter anywhere.
    hits = re.findall(rf"\b([{valid}])\b", text, re.IGNORECASE)
    return hits[-1].upper() if hits else None


def score(case: dict, response: str) -> dict:
    n = len(case["choices"])
    got = _extract_letter(response, n)
    ok = got is not None and got == str(case["answer"]).upper()
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"predicted": got, "answer": case["answer"]}}


class MMLUProAdapter:
    name = "mmlu_pro"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("mmlu_pro", SEED_CASES, full=full)

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        opts = "\n".join(f"{_LETTERS[i]}. {c}" for i, c in enumerate(case["choices"]))
        return (f"{case['question']}\n\n{opts}\n\n"
                "Answer with the single letter of the correct option on the last "
                "line (e.g. 'Answer: C').")

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
