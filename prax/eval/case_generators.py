"""Generated capability cases — a family's failure class at any size.

The hand-written capability suite has 30 cases in 10 families, most with one to
four. That is enough to grade Prax, and too few to split: a self-improvement
loop that shows its proposer some cases and keeps a change only on *other*
cases of the same family (docs/research/harness-learning.md) needs several per
family on each side. A family whose cases are parametric can be generated
instead — the failure class stays fixed, and the numbers that would let a
proposer memorize an answer change with the seed.

Generated cases carry the same deterministic checks as authored ones. Each
generator is tested both ways (tests/test_case_generators.py): the correct
answer passes every check, and the failure the family exists to catch fails.

Only the computation family is generated today: aggregates over an even-length
list (the median slip), compound percentage changes (the wrong-base slip), unit
conversion chains (the dropped unit), and plain arithmetic (the control).
"""
from __future__ import annotations

import random
from collections.abc import Callable

from prax.eval.capability import CapabilityCase, CapCheck


def family_of(case_id: str) -> str:
    """A case's family is its id prefix: ``computation_unit_conversion`` →
    ``computation``. Generated ids keep the prefix, so they join their family."""
    return (case_id or "").split("_", 1)[0]


def fmt_number(x: float) -> str:
    """``51.5``, ``96``, ``388.8``: at most two decimals, no trailing zeros."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _int_pattern(digits: str) -> str:
    """Integer digits with optional thousands separators: ``388800`` →
    ``388,?800``."""
    head = len(digits) % 3 or 3
    groups = [digits[:head]] + [digits[i:i + 3] for i in range(head, len(digits), 3)]
    return ",?".join(groups)


def number_pattern(x: float) -> str:
    """A regex for *x* as an answer may write it — ``51.5`` or ``51.50``, ``96``
    or ``96.0``, ``1,234`` or ``1234`` — and never as part of a longer number."""
    s = fmt_number(x)
    whole, _, frac = s.partition(".")
    tail = rf"\.{frac}0*" if frac else r"(?:\.0+)?"
    return rf"(?<![\d.,]){_int_pattern(whole)}{tail}(?!\.?\d)"


_UNIT = r"(?i)(cubic met(re|er)s?|m\^?3|m³)"


def _aggregate(rng: random.Random, k: int) -> CapabilityCase:
    noun = rng.choice(["readings", "scores", "daily counts", "measurements"])
    n = rng.choice([10, 12, 14])
    vals = rng.sample(range(3, 99), n)
    while sum(vals) < 100:  # the sum must not be confusable with a listed value
        vals = rng.sample(range(3, 99), n)
    s = sum(vals)
    lo, hi = sorted(vals)[n // 2 - 1], sorted(vals)[n // 2]
    median = (lo + hi) / 2
    listed = ", ".join(str(v) for v in vals)
    return CapabilityCase(
        id=f"computation_gen_aggregate_{k}",
        title="Generated: exact sum and median over an even-length list",
        prompt=(f"Here are {n} {noun}: {listed}. Give me their sum and their "
                "median. Give both as plain numbers."),
        checks=[
            CapCheck("regex", number_pattern(s), 2.0),
            CapCheck("regex", number_pattern(median), 2.0),
            # THE FAILURE: a middle element stated as the median. Only a direct
            # statement counts ("median is 47"), so a correct answer that shows
            # its work ("the median of 47 and 56 is 51.5") is not penalized.
            CapCheck("regex_absent",
                     rf"(?i)median\s*(?:is|=|:)?\s*(?:{lo}|{hi})(?![\d.])", 1.0),
        ],
        notes=f"Generated (seeded). Sum {s}; median ({lo}+{hi})/2 = {fmt_number(median)}.",
    )


def _percentage(rng: random.Random, k: int) -> CapabilityCase:
    while True:
        base = rng.choice(range(40, 420, 20))
        up, down = rng.choice([10, 20, 25, 40, 50]), rng.choice([10, 20, 25, 40, 50])
        final = base * (100 + up) * (100 - down) / 10_000
        naive = base * (100 + up - down) / 100
        stated = {base, up, down, base * (100 + up) / 100}
        # Both checked numbers must discriminate: neither may equal a number
        # the prompt states or a correct answer shows as a step, or a restated
        # figure would pass (the answer) or fail (the slip) by coincidence.
        if (abs(final * 100 - round(final * 100)) < 1e-9
                and min(abs(final - naive), *(abs(final - v) for v in stated)) > 0.005
                and min(abs(naive - v) for v in stated) > 0.005):
            break
    item = rng.choice(["subscription", "train season ticket", "gym membership", "software licence"])
    first, second = rng.sample(["January", "March", "May", "July", "September"], 2)
    if final < base:
        comparison = r"(?i)\b(less|lower|cheaper|below|down|decrease[ds]?|smaller)\b"
    else:
        comparison = r"(?i)\b(more|higher|dearer|above|up|increase[ds]?|greater)\b"
    return CapabilityCase(
        id=f"computation_gen_percentage_{k}",
        title="Generated: a compound percentage change applied to the right base",
        prompt=(f"A {item} cost {base} pounds. It rose by {up} percent in {first}, "
                f"then fell by {down} percent in {second}. What does it cost now, "
                f"and is that more or less than the original {base}?"),
        checks=[
            CapCheck("regex", number_pattern(final), 2.0),
            CapCheck("regex", comparison, 2.0),
            # THE FAILURE: adding the percentages (up − down) to the original base.
            CapCheck("regex_absent", number_pattern(naive), 1.0),
        ],
        notes=(f"Generated (seeded). {base} x {100 + up}% x {100 - down}% = "
               f"{fmt_number(final)}; the slip gives {fmt_number(naive)}."),
    )


def _unit_chain(rng: random.Random, k: int) -> CapabilityCase:
    rate = rng.choice([1.5, 2.5, 3.2, 4.5, 6.4, 7.5])
    hours = rng.choice([2, 6, 8, 12])
    litres = round(rate * 3600 * hours)
    m3 = litres / 1000
    machine = rng.choice(["pump", "filter", "irrigation line", "cooling loop"])
    return CapabilityCase(
        id=f"computation_gen_unit_{k}",
        title="Generated: a conversion chain that must not drop a unit",
        prompt=(f"A {machine} moves {fmt_number(rate)} litres per second. How many "
                f"cubic metres does it move in {hours} hours? Give the number, "
                "then the unit."),
        checks=[
            CapCheck("regex", number_pattern(m3), 2.0),
            CapCheck("regex", _UNIT, 1.0),
            # THE FAILURE: the litre figure labelled as cubic metres, or the cubic
            # metre figure labelled as litres. Showing litres as a step passes.
            CapCheck("regex_absent",
                     rf"(?i){number_pattern(litres)}\s*(cubic|m\^?3|m³)"
                     rf"|{number_pattern(m3)}\s*(l\b|litres?|liters?)", 2.0),
        ],
        notes=(f"Generated (seeded). {fmt_number(rate)} L/s x {hours * 3600} s = "
               f"{litres} L = {fmt_number(m3)} m³."),
    )


def _arithmetic(rng: random.Random, k: int) -> CapabilityCase:
    a, b, c = rng.randint(12, 39), rng.randint(12, 39), rng.randint(100, 300)
    return CapabilityCase(
        id=f"computation_gen_arithmetic_{k}",
        title="Generated: deterministic arithmetic (control)",
        prompt=f"Compute {a} * {b} + {c} and give me just the number.",
        checks=[CapCheck("regex", number_pattern(a * b + c), 1.0)],
        notes=f"Generated (seeded). {a} x {b} + {c} = {a * b + c}.",
    )


_COMPUTATION: list[Callable[[random.Random, int], CapabilityCase]] = [
    _aggregate, _percentage, _unit_chain, _arithmetic,
]

GENERATORS: dict[str, list[Callable[[random.Random, int], CapabilityCase]]] = {
    "computation": _COMPUTATION,
}


def generate_family(family: str, n: int, *, seed: int = 0) -> list[CapabilityCase]:
    """*n* cases of *family*, cycling through its generators. Deterministic in
    *seed*; an unknown family yields nothing."""
    makers = GENERATORS.get(family) or []
    if not makers or n <= 0:
        return []
    rng = random.Random(f"{family}:{seed}")
    return [makers[k % len(makers)](rng, k) for k in range(n)]
