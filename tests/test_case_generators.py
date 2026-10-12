"""Generated capability cases (prax.eval.case_generators) — keyless.

Each generated case is checked both ways, with the ground truth recomputed
here from the PROMPT TEXT rather than taken from the generator: the correct
answer must pass every check, and the failure the family exists to catch must
not. A generator whose checks accept the slip, or reject a correct answer that
shows its work, would make the loop's scoring set measure the wrong thing.
"""
from __future__ import annotations

import re

import pytest

from prax.eval.capability import CaseRun, grade_case
from prax.eval.case_generators import (
    family_of,
    fmt_number,
    generate_family,
    number_pattern,
)

CASES = generate_family("computation", 48, seed=7)


def _grade(case, answer: str) -> dict:
    return grade_case(case, CaseRun(answer=answer))


def _numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


def _answers(case) -> tuple[list[str], list[str]]:
    """(correct answers, slip answers) for *case*, derived from its prompt."""
    p = case.prompt
    if "_aggregate_" in case.id:
        vals = [int(v) for v in p.split(":", 1)[1].split(".")[0].split(",")]
        srt = sorted(vals)
        lo, hi = srt[len(vals) // 2 - 1], srt[len(vals) // 2]
        s, m = sum(vals), fmt_number((lo + hi) / 2)
        return ([f"The sum is {s} and the median is {m}.",
                 f"Sum: {s:,}. The median is the mean of {lo} and {hi}, so {m}.",
                 f"The sum is {s}; the median of {lo} and {hi} is {m}.",
                 f"Sum {s}; median ({lo} + {hi}) / 2 = {m}",
                 f"sum = {s}; median = {m}0" if "." in m else f"{s} and {m}.0"],
                [f"The sum is {s} and the median is {lo}.",
                 f"Sum: {s}. Median: {hi}"])
    if "_percentage_" in case.id:
        base, up, down = (int(x) for x in re.findall(
            r"cost (\d+) pounds\. It rose by (\d+) percent .* fell by (\d+) percent", p)[0])
        final = base * (100 + up) / 100 * (100 - down) / 100
        naive = base * (100 + up - down) / 100
        word = "less" if final < base else "more"
        return ([f"It now costs £{fmt_number(final)}, which is {word} than the original {base}.",
                 f"{base} rises to {fmt_number(base * (100 + up) / 100)}, then falls to "
                 f"{final:.2f} pounds — {word} than before."],
                [f"It now costs £{fmt_number(naive)}, {word} than the original {base}."])
    if "_unit_" in case.id:
        rate, hours = _numbers(p)[0], _numbers(p)[1]
        litres = round(rate * 3600 * hours)
        m3 = fmt_number(litres / 1000)
        return ([f"{litres:,} litres, which is {m3} cubic metres.",
                 f"{m3} m³"],
                [f"{litres} cubic metres", f"{m3} litres"])
    if "_arithmetic_" in case.id:
        a, b, c = (int(x) for x in re.findall(r"\d+", p)[:3])
        return [str(a * b + c), f"The answer is {a * b + c}."], [str(a * b + c + 10)]
    raise AssertionError(f"no answer model for {case.id}")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_a_correct_answer_passes_every_check(case):
    correct, _ = _answers(case)
    for answer in correct:
        g = _grade(case, answer)
        assert g["passed"], (answer, {k: v for k, v in g["scores"].items() if not v})


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_the_slip_the_family_catches_fails(case):
    _, slips = _answers(case)
    for answer in slips:
        assert not _grade(case, answer)["passed"], answer


def test_every_kind_is_generated_and_ids_join_the_family():
    kinds = {c.id.split("_")[2] for c in CASES}
    assert kinds == {"aggregate", "percentage", "unit", "arithmetic"}
    assert {family_of(c.id) for c in CASES} == {"computation"}
    assert len({c.id for c in CASES}) == len(CASES)


def test_deterministic_in_the_seed():
    a = generate_family("computation", 8, seed=1)
    b = generate_family("computation", 8, seed=1)
    c = generate_family("computation", 8, seed=2)
    assert [x.prompt for x in a] == [x.prompt for x in b]
    assert [x.prompt for x in a] != [x.prompt for x in c]


def test_a_family_without_a_generator_yields_nothing():
    assert generate_family("honesty", 5) == []
    assert generate_family("computation", 0) == []


@pytest.mark.parametrize("value,hits,misses", [
    (51.5, ["51.5", "is 51.50.", "=51.5"], ["51.55", "151.5", "51.6"]),
    (604, ["604", "604.0", "sum 604,"], ["1604", "6040", "604.5"]),
    (388800, ["388800", "388,800"], ["3888000"]),
    (388.8, ["388.8 m3"], ["388.88", "388,8"]),
])
def test_number_pattern(value, hits, misses):
    pat = re.compile(number_pattern(value))
    for h in hits:
        assert pat.search(h), h
    for m in misses:
        assert not pat.search(m), m


@pytest.mark.parametrize("answer,ok", [
    ("The sum is 604 and the median is 51.5.", True),
    ("Sum: 604. The median is the mean of 47 and 56: 51.5.", True),
    ("Sum 604; median (47 + 56) / 2 = 51.5", True),
    ("The sum is 604; the median of 47 and 56 is 51.5.", True),
    ("The sum is 604 and the median is 47.", False),
    ("Sum: 604. Median: 56", False),
])
def test_the_authored_median_case_credits_shown_work(answer, ok):
    """Audit-the-checker: the authored case used to fail correct answers that
    named the two middle values near the word 'median'."""
    from prax.eval.capability import load_capability_cases
    case = {c.id: c for c in load_capability_cases()}["computation_aggregate_exact"]
    assert _grade(case, answer)["passed"] is ok
