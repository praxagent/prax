"""Robust answer equivalence — "audit the check, not just the answer" in code.

The world-model diagnosis (2026-07-18) showed MATH's exact-string scorer rejecting
*correct* answers written in a different notation — `0.25` vs `\frac{1}{4}`, `5.5`
vs `\frac{11}{2}` — which made the solver look far worse than it was (a −27-point
"regression" that was pure scoring artifact). Per the axiomprover/lanyon lesson: the
*check* was the flawed part. A better checker lifts **every** benchmark's grading and
**any** Prax output compared to a reference — it spikes nothing, it just stops
under-crediting correct answers.

Core is stdlib-only (`fractions.Fraction` handles fraction↔decimal *exactly*), so it
is keyless-CI-safe. If ``sympy`` is installed, symbolic equivalence (√, expressions)
is used as a final fallback.
"""
from __future__ import annotations

import re
from fractions import Fraction


def normalize(s: str) -> str:
    """Strip LaTeX cruft / spacing so equivalent surface forms compare equal."""
    s = (s or "").strip()
    for a, b in ((r"\!", ""), (r"\,", ""), (r"\;", ""), (r"\left", ""),
                 (r"\right", ""), (r"\$", ""), ("$", ""), (r"\%", ""),
                 ("%", ""), (r"\cdot", "*"), (r"\times", "*")):
        s = s.replace(a, b)
    s = s.replace(",", "").replace(" ", "")
    # \frac{a}{b} / \dfrac{a}{b} → a/b ; \frac12 → 1/2
    s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
    s = re.sub(r"\\d?frac(\d)(\d)", r"\1/\2", s)
    s = s.strip("{}").rstrip(".").rstrip("=")
    return s.lower()


def _to_fraction(s: str) -> Fraction | None:
    """Parse a normalized answer to an exact rational, or None. ``Fraction``
    handles ``'0.25'``, ``'1/4'``, ``'5.5'``, ``'11/2'``, ``'32'`` uniformly."""
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def _sympy_equiv(a: str, b: str) -> bool:
    """Symbolic equivalence for √/expressions — only if sympy is installed."""
    try:
        from sympy import simplify
        from sympy.parsing.sympy_parser import (
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )
        tr = standard_transformations + (implicit_multiplication_application,)
        ea = parse_expr(a.replace("^", "**"), transformations=tr)
        eb = parse_expr(b.replace("^", "**"), transformations=tr)
        return bool(simplify(ea - eb) == 0)
    except Exception:  # noqa: BLE001 — sympy absent or unparseable → not equivalent
        return False


def answers_equivalent(a: str | None, b: str | None, *, tol: float = 1e-9) -> bool:
    """True iff *a* and *b* denote the same answer, across common notations.

    Order: normalized-string → exact rational (fraction↔decimal) → float tolerance
    → optional symbolic. Deterministic; never raises.
    """
    if a is None or b is None:
        return False
    na, nb = normalize(str(a)), normalize(str(b))
    if na == nb:
        return True                              # spacing / trivial formatting
    fa, fb = _to_fraction(na), _to_fraction(nb)
    if fa is not None and fb is not None:
        # Exact (0.25 == 1/4) OR within tolerance (1/3 == 0.3333…, a decimal
        # approximation of a rational).
        return fa == fb or abs(float(fa) - float(fb)) <= tol
    # One or both non-rational (e.g. a decimal approximation of an irrational).
    try:
        if abs(float(na) - float(nb)) <= tol:
            return True
    except (ValueError, TypeError):
        pass
    return _sympy_equiv(na, nb)
