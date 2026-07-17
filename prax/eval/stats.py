"""Statistical honesty for eval results — confidence intervals on pass rates.

The July 2026 external review's sharpest valid criticism: small-subset scores
(40/benchmark) were reported as bare percentages, but a 32/40 has a 95% CI of
roughly 64–91% — wide enough that many leaderboard-style comparisons are not
statistically defensible. Every aggregate now carries its interval so a reader
can see what the number does and doesn't establish.

Wilson score interval (not normal approximation): well-behaved at extreme rates
(40/40 → [91.2%, 100%], not [100%, 100%]) and at small n, with no scipy
dependency. Reference: Wilson (1927); recommended by Brown/Cai/DasGupta (2001)
over the Wald interval.
"""
from __future__ import annotations

import math

# z for a 95% two-sided interval.
_Z95 = 1.959963984540054


def wilson_ci(passed: int, total: int, z: float = _Z95) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Returns ``(low, high)`` in [0, 1]. ``total <= 0`` → (0.0, 1.0) (no data,
    no claim).
    """
    if total <= 0:
        return (0.0, 1.0)
    p = passed / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total))
    return (max(0.0, center - half), min(1.0, center + half))


def attach_ci(aggregate: dict) -> dict:
    """Attach ``pass_rate_ci95`` + ``n`` to an aggregate that has passed/graded.

    Mutates and returns *aggregate*. No-op when the counts aren't present, so
    it's safe to call on any adapter's aggregate shape.
    """
    passed, graded = aggregate.get("passed"), aggregate.get("graded")
    if isinstance(passed, int) and isinstance(graded, int) and graded > 0:
        low, high = wilson_ci(passed, graded)
        aggregate["n"] = graded
        aggregate["pass_rate_ci95"] = [round(low, 4), round(high, 4)]
        # The one-line honest rendering, e.g. "80.0% (n=40, 95% CI 64.4–90.9%)"
        aggregate["pass_rate_str"] = (
            f"{100 * passed / graded:.1f}% (n={graded}, "
            f"95% CI {100 * low:.1f}–{100 * high:.1f}%)"
        )
    return aggregate
