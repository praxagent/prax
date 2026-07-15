"""Key-free tests for the HumanEval code-generation adapter.

The real scorer runs code in the sandbox; here the executor is INJECTED (fake) so
the extract → assemble → verdict pipeline is tested deterministically without a
container or keys. Live sandbox execution is verified separately (see the PR).
"""
from __future__ import annotations

from prax.eval.benchmarks.humaneval import (
    SEED_CASES,
    HumanEvalAdapter,
    build_program,
    extract_code,
)


def _fake_executor(passed: bool):
    def _exec(program: str, timeout: int = 30) -> dict:
        # record the program so tests can assert it was assembled correctly
        _exec.last_program = program
        return {"passed": passed, "output": "__HE_OK__" if passed else "AssertionError"}
    return _exec


def test_extract_code_prefers_defining_fenced_block():
    resp = ("Sure!\n```python\nimport math\n```\n"
            "```python\ndef add(a, b):\n    return a + b\n```")
    code = extract_code(resp, "add")
    assert "def add" in code and "import math" not in code


def test_extract_code_falls_back_to_raw():
    resp = "def add(a, b):\n    return a + b"
    assert "def add" in extract_code(resp, "add")


def test_build_program_includes_test_and_call():
    case = SEED_CASES[0]
    prog = build_program(case, case["canonical_solution"])
    assert "def check(candidate)" in prog
    assert f"check({case['entry_point']})" in prog


def test_score_passes_with_fake_pass_executor():
    ad = HumanEvalAdapter(executor=_fake_executor(True))
    case = SEED_CASES[0]
    res = ad.score(case, f"```python\n{case['canonical_solution']}```")
    assert res["passed"] is True and res["score"] == 1.0


def test_score_fails_with_fake_fail_executor():
    ad = HumanEvalAdapter(executor=_fake_executor(False))
    res = ad.score(SEED_CASES[0], "```python\ndef add(a, b):\n    return a - b\n```")
    assert res["passed"] is False and res["score"] == 0.0


def test_assembled_program_carries_candidate_and_test():
    fake = _fake_executor(True)
    ad = HumanEvalAdapter(executor=fake)
    case = SEED_CASES[0]
    ad.score(case, f"```python\n{case['canonical_solution']}```")
    prog = fake.last_program
    assert "return a + b" in prog          # candidate code
    assert "def check(candidate)" in prog  # the test
    assert "check(add)" in prog            # the invocation


def test_prompt_shows_signature():
    ad = HumanEvalAdapter()
    p = ad.prompt(SEED_CASES[0])
    assert "def add" in p and "function" in p.lower()
