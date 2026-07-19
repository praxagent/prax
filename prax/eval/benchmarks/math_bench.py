"""MATH — competition mathematics (Hendrycks et al., arXiv 2103.03874).

The step above GSM8K: problems from AMC/AIME-style competitions across algebra,
number theory, geometry, precalculus. Answers are exact expressions the model is
asked to place in \\boxed{...}; grading is deterministic — extract the boxed answer
(or the final answer) and compare after light normalization — no LLM judge.

Inline keyless seed set here (hand-authored representative problems, answers
verified); the full 5k-problem test set loads from PRAX_EVAL_DIR (never committed —
benchmark-contamination firewall). Module named ``math_bench`` so it never shadows
the stdlib ``math``.
"""
from __future__ import annotations

import re

SEED_CASES: list[dict] = [
    {"id": "math_pow", "problem": "Evaluate $2^5$.", "answer": "32"},
    {"id": "math_lin", "problem": "Solve for $x$: $3x + 7 = 22$.", "answer": "5"},
    {"id": "math_fact", "problem": "Compute $\\dfrac{7!}{5!}$.", "answer": "42"},
    {"id": "math_sqrt", "problem": "Simplify $\\sqrt{144}$.", "answer": "12"},
    {"id": "math_quad",
     "problem": "If $f(x) = x^2 - 4x + 4$, find $f(3)$.", "answer": "1"},
    {"id": "math_sum",
     "problem": "What is the sum of the first 10 positive integers?", "answer": "55"},
]

_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _extract_boxed(text: str) -> str | None:
    r"""Extract the LAST ``\boxed{...}`` content with **balanced braces**.

    A regex like ``\\boxed\{([^{}]*)\}`` silently fails on nested braces — which
    is *every* fraction/matrix/vector answer (``\boxed{\frac{1}{4}}``) — and then
    fallback extraction grabs a stray digit (the denominator!). This scans matched
    braces so the full structured answer survives. (Audit the check.)
    """
    marker = r"\boxed{"
    found: list[str] = []
    i = 0
    while True:
        j = text.find(marker, i)
        if j < 0:
            break
        k = j + len(marker)
        depth = 1
        while k < len(text) and depth > 0:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
            k += 1
        if depth == 0:
            found.append(text[j + len(marker):k - 1])
        i = k
    return found[-1] if found else None


def _normalize(s: str) -> str:
    """Light normalization so '32', '32.', '$32$', ' 32 ' all compare equal."""
    s = (s or "").strip()
    s = s.replace("$", "").replace(r"\!", "").replace(" ", "")
    s = s.replace(",", "").rstrip(".")
    s = s.replace(r"\left", "").replace(r"\right", "")
    # Collapse \dfrac/\frac{a}{b} → a/b for simple comparisons.
    s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
    return s.lower()


def _extract_answer(response: str) -> str | None:
    text = response or ""
    boxed = _extract_boxed(text)
    if boxed is not None:
        return boxed
    m = re.search(r"answer\s*(?:is|:)?\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if m:
        # Prefer a number inside the tail if present, else the tail itself.
        nums = _NUM.findall(m.group(1))
        return nums[-1] if nums else m.group(1).strip()
    nums = _NUM.findall(text)
    return nums[-1] if nums else None


def score(case: dict, response: str) -> dict:
    from prax.eval.answer_equiv import answers_equivalent

    got = _extract_answer(response)
    want = case["answer"]
    # Robust equivalence (fraction↔decimal, spacing, LaTeX, optional symbolic) —
    # so a correct answer in different notation (0.25 vs \frac{1}{4}) isn't marked
    # wrong. See prax/eval/answer_equiv.py.
    ok = got is not None and answers_equivalent(got, want)
    return {"passed": ok, "score": 1.0 if ok else 0.0,
            "checks": {"predicted": got, "answer": want}}


class MATHAdapter:
    name = "math"
    variant = "MATH-500 subset, exact/equivalent final answer"

    def __init__(self, cases: list[dict] | None = None, full: bool = False):
        from prax.eval.benchmarks.datasets import cases_for
        self._cases = cases if cases is not None else cases_for("math", SEED_CASES, full=full)

    def cases(self) -> list[dict]:
        return self._cases

    def prompt(self, case: dict) -> str:
        return (f"{case['problem']}\n\n"
                "Work through it, then give the final answer in \\boxed{...} on the "
                "last line.")

    def score(self, case: dict, response: str) -> dict:
        return score(case, response)
