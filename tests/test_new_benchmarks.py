"""Key-free tests for the added standard benchmarks: MMLU-Pro, GPQA, MATH, SimpleQA.

Deterministic scoring only — no LLM, no keys. Verifies the registry wiring, that
every seed case passes its own reference answer (catches a bad answer key), and
that the extractors handle the response shapes models actually produce.
"""
from __future__ import annotations

import pytest

from prax.eval.benchmarks import get_adapter

NEW = ("mmlu_pro", "gpqa", "math", "simpleqa")


def _correct_response(name: str, case: dict) -> str:
    if name in ("mmlu_pro", "gpqa"):
        return f"Reasoning about the options... Answer: {case['answer']}"
    if name == "math":
        return f"Working it out... the final answer is \\boxed{{{case['answer']}}}"
    return f"{case['answers'][0]}"


@pytest.mark.parametrize("name", NEW)
def test_registered_and_nonempty(name):
    ad = get_adapter(name)
    assert ad.name == name
    assert ad.cases()
    for case in ad.cases():
        assert ad.prompt(case).strip()


@pytest.mark.parametrize("name", NEW)
def test_every_seed_case_passes_its_own_answer(name):
    ad = get_adapter(name)
    for case in ad.cases():
        res = ad.score(case, _correct_response(name, case))
        assert res["passed"] is True, f"{name}/{case['id']} should pass: {res}"
        assert res["score"] == 1.0


@pytest.mark.parametrize("name", NEW)
def test_wrong_answer_fails(name):
    ad = get_adapter(name)
    case = ad.cases()[0]
    wrong = {
        "mmlu_pro": "Answer: " + ("B" if case.get("answer") == "A" else "A"),
        "gpqa": "Answer: " + ("B" if case.get("answer") == "A" else "A"),
        "math": "The answer is \\boxed{-999999}",
        "simpleqa": "I'm not sure, possibly qwertytown.",
    }[name]
    res = ad.score(case, wrong)
    assert res["passed"] is False and res["score"] == 0.0


def test_mmlu_pro_letter_extraction_variants():
    ad = get_adapter("mmlu_pro")
    case = next(c for c in ad.cases() if c["answer"] == "D")  # mmlup_ph
    for resp in (f"The answer is {case['answer']}.",
                 f"... so I choose ({case['answer']}).",
                 f"option {case['answer']}",
                 f"Final line:\n{case['answer']}"):
        assert ad.score(case, resp)["passed"], resp


def test_math_boxed_and_numeric_fallback():
    ad = get_adapter("math")
    case = next(c for c in ad.cases() if c["answer"] == "32")  # math_pow
    assert ad.score(case, "so 2^5 = \\boxed{32}")["passed"]
    assert ad.score(case, "the answer is 32")["passed"]          # no box → tail number
    assert not ad.score(case, "I think it is 31")["passed"]


def test_simpleqa_alias_and_substring():
    ad = get_adapter("simpleqa")
    case = next(c for c in ad.cases() if c["id"] == "sqa_romeo")
    assert ad.score(case, "It was written by William Shakespeare.")["passed"]
    assert ad.score(case, "Shakespeare wrote it.")["passed"]     # alias
    assert not ad.score(case, "It was Christopher Marlowe.")["passed"]


def test_simpleqa_abstention_does_not_pass():
    ad = get_adapter("simpleqa")
    case = ad.cases()[0]
    assert not ad.score(case, "I don't know.")["passed"]
