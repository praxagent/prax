"""Keyless tests for the world-model reasoning loop (no LLM, no network)."""
from __future__ import annotations

from prax.reasoning.worldmodel import run_code, world_model_solve


def test_run_code_captures_final_and_timeout():
    r = run_code('print("FINAL:", 6 * 7)')
    assert r.returncode == 0 and "FINAL: 42" in r.stdout
    t = run_code("while True:\n    pass", timeout=1.0)
    assert t.timed_out and t.returncode == -1


def test_solve_runs_model_program():
    fake = lambda p: "```python\nprint('FINAL:', sum(range(11)))\n```"  # noqa: E731
    res = world_model_solve("sum 0..10", fake)
    assert res["answer"] == "55" and res["via"] == "code" and res["rounds"] == 1


def test_solve_repairs_on_failure():
    calls = {"n": 0}

    def flaky(_p):
        calls["n"] += 1
        if calls["n"] == 1:
            return "```python\nprint(FINAL)\n```"        # NameError
        return "```python\nprint('FINAL:', 42)\n```"     # fixed

    res = world_model_solve("x", flaky, max_rounds=3)
    assert res["answer"] == "42" and res["via"] == "code" and res["rounds"] == 2


def test_solve_falls_back_to_text_when_no_code():
    res = world_model_solve("x", lambda _p: "I think the answer is\nFINAL: seven")
    assert res["via"] == "text" and res["answer"] == "seven"
