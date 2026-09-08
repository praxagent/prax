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


def test_simpleqa_numeric_format_robustness():
    # Regression (found by the first live run): a comma-grouped number must match
    # its comma-free reference — a format mismatch is not a wrong answer.
    ad = get_adapter("simpleqa")
    speed = next(c for c in ad.cases() if c["id"] == "sqa_speed")
    # The reference must be the correct value, not the old wrong 299,000,000.
    assert "299792458" in speed["answers"]
    assert "299000000" not in speed["answers"]
    for resp in ("The speed of light is 299,792,458 m/s.",
                 "about 299792458 metres per second",
                 "roughly 3.00e8 m/s"):
        assert ad.score(speed, resp)["passed"], resp


# ── Infra-failure accounting: an executor failure must be EXCLUDED, not scored ──

class _Run:
    def __init__(self, answer="", error=""):
        self.answer, self.error, self.tokens = answer, error, 0


def test_executor_failure_detects_swallowed_orchestrator_error():
    from prax.eval.benchmarks import _executor_failure
    # The orchestrator swallows a provider error into a friendly answer — must be
    # detected as a failure, not graded as the wrong-answer "401".
    swallowed = _Run(answer="I hit an internal error while working on that request. "
                            "Error: AuthenticationError: Error code: 401")
    assert _executor_failure(swallowed) is not None
    assert _executor_failure(_Run(error="Timeout")) == "Timeout"
    # A real answer (even one containing a number) is NOT a failure.
    assert _executor_failure(_Run(answer="The answer is 72.")) is None
    # An honest empty answer is a real miss (score 0), not an infra error.
    assert _executor_failure(_Run(answer="")) is None


def test_failed_cases_are_excluded_from_pass_rate(monkeypatch, tmp_path):
    """Two halves of one contract (prax.eval.is_infrastructure_error):

    1. A run where every call fails on OUR credentials grades nothing — the
       cases are infra exclusions, reported as such (the voided first matrix
       parsed 401s as wrong answers → a fake 0.00).
    2. A run that fails HARDER can never score BETTER: an agent-attributable
       error is a failure in the denominator, exactly like a wrong answer. The
       old aggregator dropped every error, so crashing on the hard cases raised
       the pass rate.
    """
    from prax.eval.benchmarks import get_adapter, run_benchmark
    from prax.eval.rate_limit import ExecutorError

    def always_fail(_prompt):
        raise ExecutorError("401 Missing Authentication header", transient=False)

    adapter = get_adapter("gsm8k")  # seed set, keyless
    summary = run_benchmark(adapter, always_fail, out_dir=tmp_path / "auth", resume=False)
    agg = summary["aggregate"]
    assert agg["graded"] == 0            # nothing was scored
    assert agg["errors"] == agg["excluded_infra"] == agg["attempted"] > 0
    assert agg["errored_as_failure"] == 0
    assert agg["pass_rate"] == 0.0       # (0/0 → 0.0, but graded is 0 — not a real score)

    # The invariant. Same adapter; the agent gets one case right and, on the
    # rest, either answers wrong or blows up (not an infra pattern).
    first = adapter.prompt(adapter.cases()[0])
    right = adapter.cases()[0]["answer"]

    def wrong_elsewhere(prompt):
        return right if prompt == first else "no idea"

    def crash_elsewhere(prompt):
        if prompt == first:
            return right
        raise RuntimeError("agent blew up mid-turn")

    wrong = run_benchmark(adapter, wrong_elsewhere, out_dir=tmp_path / "wrong", resume=False)["aggregate"]
    crashed = run_benchmark(adapter, crash_elsewhere, out_dir=tmp_path / "crash", resume=False)["aggregate"]
    assert crashed["graded"] == crashed["attempted"] == wrong["graded"]  # nothing dropped
    assert crashed["errored_as_failure"] == crashed["attempted"] - 1
    assert crashed["excluded_infra"] == 0
    assert crashed["pass_rate"] <= wrong["pass_rate"]  # failing harder never scores better
    assert crashed["pass_rate"] == wrong["pass_rate"] == round(1 / wrong["attempted"], 3)


def test_resolved_dataset_reflects_actual_load(monkeypatch, tmp_path):
    import prax.eval.benchmarks.datasets as ds
    # Flag off → always "seed".
    monkeypatch.delenv("PRAX_EVAL_FULL_DATASETS", raising=False)
    assert ds.resolved_dataset("gsm8k") == "seed"
    # Flag on but NO cache file → still "seed" (the honest label; the flag alone lied).
    monkeypatch.setenv("PRAX_EVAL_FULL_DATASETS", "1")
    monkeypatch.setattr(ds, "_cache_path", lambda name: tmp_path / f"{name}.jsonl")
    assert ds.resolved_dataset("bfcl") == "seed"
    # Flag on AND a cache exists → "real".
    (tmp_path / "gsm8k.jsonl").write_text('{"id":"x","question":"q","answer":"1"}\n')
    assert ds.resolved_dataset("gsm8k") == "real"


# ── Task-budget timeout scores 0 (real miss), auth failure is excluded ───────

def test_task_timeout_is_agent_attributable_not_infra():
    # The ONE attribution rule (replaced a benchmark-local timeout classifier):
    # a task-budget timeout is the agent's outcome; provider auth is ours.
    from prax.eval import is_infrastructure_error
    assert is_infrastructure_error("agent run exceeded 120s maximum runtime") is False
    assert is_infrastructure_error("I hit a turn timeout while working on that request") is False
    assert is_infrastructure_error("task exceeded 120.0s wall-clock limit") is False
    assert is_infrastructure_error("401 Missing Authentication header") is True


def test_task_timeout_scores_zero_not_excluded(monkeypatch):
    # A task-budget timeout must fall through as an EMPTY answer — scored 0, NOT
    # raised as an ExecutorError (which would exclude it and retry it 4x), and
    # not the failure text either: "exceeded 120s" carries a digit a numeric
    # grader would happily extract.
    import prax.eval.benchmarks as bench
    from prax.eval.capability import CaseRun

    def fake_executor(prompt, **kw):
        return CaseRun(answer="I hit a turn timeout while working on that request: "
                              "agent run exceeded 120s maximum runtime.")
    monkeypatch.setattr("prax.eval.capability.orchestrator_executor", fake_executor)
    replay = bench.live_orchestrator_replay(tier="low")
    assert replay("solve this") == ""


def test_auth_failure_still_raises_and_excludes(monkeypatch):
    import prax.eval.benchmarks as bench
    from prax.eval.capability import CaseRun
    from prax.eval.rate_limit import ExecutorError

    def fake_executor(prompt, **kw):
        return CaseRun(answer="I hit an internal error while working on that request. "
                              "Error: AuthenticationError: 401")
    monkeypatch.setattr("prax.eval.capability.orchestrator_executor", fake_executor)
    replay = bench.live_orchestrator_replay(tier="low")
    with pytest.raises(ExecutorError):
        replay("solve this")


# ── Terminal-Bench adapter (sandbox-scored task completion) ──────────────────

def _local_bash_executor(program, timeout=30):
    """Keyless test executor: run the assembled program on the host in bash instead
    of the sandbox — the scoring logic is identical, no container needed."""
    import subprocess
    try:
        r = subprocess.run(["bash", "-c", program], capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "output": str(exc)}
    out = (r.stdout or "") + (r.stderr or "")
    return {"passed": "__TB_OK__" in out, "output": out[:500]}


def test_terminal_bench_registered_and_canonical_solutions_pass():
    from prax.eval.benchmarks import get_adapter
    from prax.eval.benchmarks.terminal_bench import SEED_CASES
    adapter = get_adapter("terminal_bench", executor=_local_bash_executor)
    assert adapter.name == "terminal_bench"
    assert len(adapter.cases()) >= 5
    # Every seed task's own canonical solution must satisfy its hidden verify.
    for case in SEED_CASES:
        resp = f"```bash\n{case['canonical_solution']}\n```"
        result = adapter.score(case, resp)
        assert result["passed"] is True, f"canonical solution failed for {case['id']}: {result}"
        assert result["score"] == 1.0


def test_terminal_bench_wrong_solution_fails():
    from prax.eval.benchmarks import get_adapter
    from prax.eval.benchmarks.terminal_bench import SEED_CASES
    adapter = get_adapter("terminal_bench", executor=_local_bash_executor)
    case = SEED_CASES[0]  # tb_greeting
    result = adapter.score(case, "```bash\necho definitely wrong > other.txt\n```")
    assert result["passed"] is False and result["score"] == 0.0


def test_terminal_bench_extracts_fenced_script():
    from prax.eval.benchmarks.terminal_bench import extract_script
    assert extract_script("Here you go:\n```bash\nls -la\n```\nDone.") == "ls -la"
    assert extract_script("```\necho hi\n```") == "echo hi"
    # No fence → raw text (best effort)
    assert extract_script("echo raw").strip() == "echo raw"


def test_terminal_bench_sandbox_absent_is_graceful(monkeypatch):
    # With no injected executor and sandbox disabled, scoring must fail cleanly, not raise.
    from prax.eval.benchmarks.terminal_bench import _sandbox_executor
    from prax.settings import settings
    monkeypatch.setattr(type(settings), "sandbox_available", property(lambda self: False))
    result = _sandbox_executor("echo __TB_OK__")
    assert result["passed"] is False and "sandbox" in result["output"].lower()
