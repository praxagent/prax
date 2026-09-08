"""Every aggregator obeys ONE error-attribution rule — not just the capability suite.

``tests/test_eval_error_accounting.py`` pinned the contract for
``summarize_capability_results``: an errored case is excluded from the pass rate
only when ``is_infrastructure_error`` says the environment failed; every other
error is a FAILURE that keeps its tokens on the cost axis, and both buckets are
reported. That function had exactly one caller. The three other aggregators —
the benchmark adapters' ``run_benchmark``, ``run_benchmark_lift``, and the GAIA
suite — still dropped every result carrying ``error`` from the numerator, the
denominator AND the token sum, so on the surfaces that feed the public
``MATRIX.md`` a run that crashed on its hardest cases outscored one that
answered them wrong. These tests pin the repaired contract on all three, and on
the live replay that decides what reaches them.

Keyless: fake executors, fake usage, tmp dirs. Where a test asserts a behaviour
the old code got wrong, the comment says which assertion the old code fails.
"""
from __future__ import annotations

import contextlib
import json
import re

import pytest

from prax.eval import is_infrastructure_error
from prax.eval.benchmarks import (
    get_adapter,
    live_orchestrator_replay,
    run_benchmark,
    run_benchmark_lift,
)
from prax.eval.capability import CaseRun
from prax.eval.rate_limit import ExecutorError

AGENT_ERR = "TimeoutError: task exceeded 180.0s wall-clock limit"
INFRA_ERR = "ConnectionError: connection refused"

# Synthetic cases: the answer is the number in the question, so a "correct"
# executor needs no benchmark knowledge.
CASES = [{"id": f"q{i}", "question": f"q{i}", "answer": str(i)} for i in (1, 2, 3)]


def _num(prompt: str) -> str:
    return re.search(r"q(\d+)", prompt).group(1)


def _adapter():
    return get_adapter("gsm8k", cases=CASES)


def _second(prompt: str) -> bool:
    return _num(prompt) == "2"


# --------------------------------------------------------------------------- #
# The attribution rule itself
# --------------------------------------------------------------------------- #

class TestAttribution:
    @pytest.mark.parametrize("err", [
        "ExecutorError: 401 Missing Authentication header",
        "403 Forbidden",
        "HTTP 403 Forbidden",
        "Error code: 429 - rate limit",
        "AuthenticationError: Unauthorized",
        INFRA_ERR,
    ])
    def test_provider_rejecting_our_credentials_is_infrastructure(self, err):
        # A missing/revoked key is the harness's fault — the first matrix run
        # scored these as wrong answers. Old classifier: 401/403 → False.
        assert is_infrastructure_error(err) is True

    @pytest.mark.parametrize("err", [
        AGENT_ERR, "RuntimeError: agent blew up mid-turn",
        "I hit a turn timeout while working on that request",
    ])
    def test_agent_outcomes_are_not_infrastructure(self, err):
        assert is_infrastructure_error(err) is False

    @pytest.mark.parametrize("err", [
        "TimeoutError: agent run exceeded 403s maximum runtime",
        "RecursionError: recursion limit of 401 reached",
        "ValueError: expected 1403 tokens",
        "the file has 401 lines",
        "HTTP 4010",
    ])
    def test_status_digits_outside_an_http_context_are_not_infrastructure(self, err):
        # The digits alone are not a verdict. As bare substrings, "401"/"403"/
        # "429" matched these agent failures and EXCLUDED them from the score
        # (the orchestrator's own max-runtime message carries "403s" whenever
        # that cap is 403 s). Old classifier: True for every one of these.
        assert is_infrastructure_error(err) is False


# --------------------------------------------------------------------------- #
# run_benchmark — the adapters that feed MATRIX.md
# --------------------------------------------------------------------------- #

class TestRunBenchmark:
    def _run(self, replay, tmp_path, name):
        return run_benchmark(_adapter(), replay, out_dir=tmp_path / name, resume=False)

    def test_agent_error_is_a_failure_in_the_denominator(self, tmp_path):
        def crash_on_second(prompt):
            if _second(prompt):
                raise RuntimeError("agent blew up mid-turn")
            return _num(prompt)

        agg = self._run(crash_on_second, tmp_path, "crash")["aggregate"]
        assert agg["graded"] == agg["attempted"] == 3      # old code: graded == 2
        assert agg["errored_as_failure"] == 1 and agg["excluded_infra"] == 0
        assert agg["errors"] == 1                            # run-health signal intact
        assert agg["pass_rate"] == round(2 / 3, 3)           # old code: 1.0
        assert agg["avg_score"] == round(2 / 3, 3)
        assert "scored as failure (agent error)" in agg["pass_rate_str"]

    def test_failing_harder_never_scores_better(self, tmp_path):
        def wrong_on_second(prompt):
            return "no idea" if _second(prompt) else _num(prompt)

        def crash_on_second(prompt):
            if _second(prompt):
                raise RuntimeError("agent blew up mid-turn")
            return _num(prompt)

        wrong = self._run(wrong_on_second, tmp_path, "wrong")["aggregate"]
        crashed = self._run(crash_on_second, tmp_path, "crash")["aggregate"]
        assert crashed["pass_rate"] <= wrong["pass_rate"]    # old code: 1.0 > 0.667
        assert crashed["pass_rate"] == wrong["pass_rate"]

    def test_timeout_whose_message_contains_status_digits_is_still_a_failure(
            self, tmp_path, monkeypatch):
        """The orchestrator's max-runtime message is "agent run exceeded {N}s
        maximum runtime"; with N == 403 the bare-substring classifier called it
        an infra fault and dropped the case, so the crash run scored 1.0 against
        the wrong-answer run's 0.667. Constructed end-to-end here."""
        monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "0")  # no backoff sleeps

        def wrong_on_second(prompt):
            return "no idea" if _second(prompt) else _num(prompt)

        def timeout_on_second(prompt):
            if _second(prompt):
                raise TimeoutError("agent run exceeded 403s maximum runtime")
            return _num(prompt)

        wrong = self._run(wrong_on_second, tmp_path, "wrong")["aggregate"]
        timed_out = self._run(timeout_on_second, tmp_path, "timeout")["aggregate"]
        assert timed_out["excluded_infra"] == 0                       # old code: 1
        assert timed_out["errored_as_failure"] == 1
        assert timed_out["graded"] == 3                               # old code: 2
        assert timed_out["pass_rate"] <= wrong["pass_rate"]           # old code: 1.0 > 0.667
        assert timed_out["pass_rate"] == wrong["pass_rate"] == round(2 / 3, 3)

    def test_infra_error_is_excluded_and_reported(self, tmp_path):
        def refused_on_second(prompt):
            if _second(prompt):
                raise ExecutorError(INFRA_ERR, transient=False)
            return _num(prompt)

        agg = self._run(refused_on_second, tmp_path, "infra")["aggregate"]
        assert agg["graded"] == 2 and agg["attempted"] == 3
        assert agg["excluded_infra"] == 1 and agg["errored_as_failure"] == 0
        assert agg["errors"] == 1
        assert agg["pass_rate"] == 1.0
        assert "excluded (infra)" in agg["pass_rate_str"]    # old code: no such key/caveat

    def test_agent_error_tokens_stay_on_the_cost_axis(self, tmp_path, monkeypatch):
        """The failed attempt's tokens must not vanish with the case."""
        import prax.eval.telemetry as telemetry

        class _Usage:
            def snapshot(self):
                return {"prompt_tokens": 700_000, "completion_tokens": 27_000}

        @contextlib.contextmanager
        def fake_collect_usage():
            yield _Usage()

        monkeypatch.setattr(telemetry, "collect_usage", fake_collect_usage)

        def crash_on_second(prompt):
            if _second(prompt):
                raise RuntimeError("agent blew up after burning tokens")
            return _num(prompt)

        summary = self._run(crash_on_second, tmp_path, "tokens")
        agg = summary["aggregate"]
        assert agg["total_tokens"] == 3 * 727_000            # old code: 2 * 727_000
        row = json.loads((tmp_path / "tokens" / "results" / "q2.json").read_text())
        assert row["error"].startswith("RuntimeError:")
        assert row["prompt_tokens"] == 700_000               # old code: no tokens on the error row

    def test_error_row_keeps_run_batch_resume_semantics(self, tmp_path):
        """Catching the exception inside _run_one must not change what
        ``retry_errors`` re-runs: the row carries the same ``error`` key."""
        from prax.eval.batch import _completed_ids

        def crash_on_second(prompt):
            if _second(prompt):
                raise RuntimeError("agent blew up mid-turn")
            return _num(prompt)

        self._run(crash_on_second, tmp_path, "resume")
        results_dir = tmp_path / "resume" / "results"
        assert _completed_ids(results_dir, retry_errors=False) == {"q1", "q2", "q3"}
        assert _completed_ids(results_dir, retry_errors=True) == {"q1", "q3"}


# --------------------------------------------------------------------------- #
# run_benchmark_lift — the paired full-vs-bare comparison
# --------------------------------------------------------------------------- #

class TestRunBenchmarkLift:
    def _lift(self, tmp_path, monkeypatch, *, full_second: CaseRun, name: str):
        import prax.eval.capability as cap

        def full(prompt, **kw):
            return full_second if _second(prompt) else CaseRun(answer=_num(prompt), tokens=100)

        def bare(prompt, **kw):
            return CaseRun(answer="no idea", tokens=50)

        monkeypatch.setattr(cap, "orchestrator_executor", full)
        monkeypatch.setattr(cap, "bare_executor", bare)
        return run_benchmark_lift("gsm8k", out_dir=tmp_path / name, resume=False,
                                  cases=CASES)["aggregate"]

    def test_agent_error_on_the_full_arm_costs_the_lift(self, tmp_path, monkeypatch):
        agg = self._lift(tmp_path, monkeypatch, name="agent",
                         full_second=CaseRun(answer="", error=AGENT_ERR, tokens=727_000))
        assert agg["cases"] == 3 and agg["attempted"] == 3   # old code: cases == 2
        assert agg["errored_as_failure"] == 1 and agg["excluded_infra"] == 0
        assert agg["full_pass_rate"] == round(2 / 3, 3)      # old code: 1.0
        assert agg["bare_pass_rate"] == 0.0
        assert agg["harness_lift"] == round(2 / 3, 3)
        assert agg["avg_full_tokens"] == round((100 + 100 + 727_000) / 3)  # old code: 100

    def test_failing_harder_never_lifts_more(self, tmp_path, monkeypatch):
        wrong = self._lift(tmp_path, monkeypatch, name="wrong",
                           full_second=CaseRun(answer="no idea", tokens=100))
        crashed = self._lift(tmp_path, monkeypatch, name="crash",
                             full_second=CaseRun(answer="", error=AGENT_ERR, tokens=100))
        assert crashed["harness_lift"] <= wrong["harness_lift"]   # old code: 1.0 > 0.667
        assert crashed["full_pass_rate"] == wrong["full_pass_rate"]

    def test_infra_error_drops_the_pair_and_reports_it(self, tmp_path, monkeypatch):
        agg = self._lift(tmp_path, monkeypatch, name="infra",
                         full_second=CaseRun(answer="", error=INFRA_ERR))
        assert agg["cases"] == 2 and agg["attempted"] == 3
        assert agg["excluded_infra"] == 1 and agg["errored_as_failure"] == 0
        assert agg["full_pass_rate"] == 1.0 and agg["harness_lift"] == 1.0


# --------------------------------------------------------------------------- #
# run_gaia_suite — the resumable GAIA batch
# --------------------------------------------------------------------------- #

def _gaia_result(task_id: str, *, passed: bool = False, error: str | None = None,
                 tokens: int = 100) -> dict:
    grade = {"pass": passed, "match_type": "exact" if passed else "none"}
    if error:
        grade.update(crashed=True, error=error)
        grade["pass"] = False
    return {"task_id": task_id, "run_id": "r", "run_dir": "/dev/null", "grade": grade,
            "cost": {"total_tokens": tokens, "llm_calls": 1}, "response": "",
            "duration_s": 1.0, "meta": {}}


class TestRunGaiaSuite:
    def _suite(self, tmp_path, monkeypatch, canned: dict, name: str):
        import prax.eval.gaia_single as gs

        monkeypatch.setattr(gs, "ensure_eval_dir", lambda _d: _d)  # never touch $PRAX_EVAL_DIR

        def fake_run_gaia_task(task_id, **kw):
            item = canned[task_id]
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(gs, "run_gaia_task", fake_run_gaia_task)
        return gs.run_gaia_suite(task_ids=list(canned), suite_dir=tmp_path / name,
                                 resume=False)["aggregate"]

    def test_every_error_channel_is_attributed(self, tmp_path, monkeypatch):
        canned = {
            "t-pass": _gaia_result("t-pass", passed=True, tokens=100),
            "t-fail": _gaia_result("t-fail", tokens=200),
            # Errors caught INSIDE run_gaia_task (grade.error) …
            "t-timeout": _gaia_result("t-timeout", error="TIMEOUT: " + AGENT_ERR, tokens=727_000),
            "t-infra": _gaia_result("t-infra", error="ConnectionRefusedError: Connection refused",
                                    tokens=5_000),
            # … and errors where run_gaia_task itself raised (run_batch's error row).
            "t-raise": RuntimeError("harness bug"),
            "t-raise-infra": OSError("[Errno 28] No space left on device"),
        }
        agg = self._suite(tmp_path, monkeypatch, canned, "all")
        assert agg["attempted"] == 6
        assert agg["excluded_infra"] == 2                    # old code: no such key
        assert agg["graded"] == 4                            # old code: 5 (t-raise dropped, t-infra blamed)
        assert agg["errored_as_failure"] == 2                # timeout + unrecognised harness error
        assert agg["passed"] == 1 and agg["pass_rate"] == 0.25
        # The timeout's 727k tokens stay on the cost axis; the infra crash's do not.
        assert agg["total_tokens"] == 100 + 200 + 727_000
        assert "scored as failure (agent error)" in agg["pass_rate_str"]
        assert "excluded (infra)" in agg["pass_rate_str"]

    def test_failing_harder_never_scores_better(self, tmp_path, monkeypatch):
        base = {"t-pass": _gaia_result("t-pass", passed=True)}
        wrong = self._suite(tmp_path, monkeypatch,
                            {**base, "t-x": _gaia_result("t-x")}, "wrong")
        raised = self._suite(tmp_path, monkeypatch,
                             {**base, "t-x": RuntimeError("harness bug")}, "raised")
        assert raised["pass_rate"] <= wrong["pass_rate"]     # old code: 1.0 > 0.5
        assert raised["pass_rate"] == wrong["pass_rate"] == 0.5


# --------------------------------------------------------------------------- #
# live_orchestrator_replay — what reaches the benchmark aggregator
# --------------------------------------------------------------------------- #

class TestLiveReplayAttribution:
    def _replay(self, monkeypatch, run: CaseRun):
        monkeypatch.setattr("prax.eval.capability.orchestrator_executor",
                            lambda prompt, **kw: run)
        return live_orchestrator_replay(tier="low")

    def test_infra_failure_raises_so_the_batch_records_an_error(self, monkeypatch):
        with pytest.raises(ExecutorError):
            self._replay(monkeypatch, CaseRun(answer="", error=INFRA_ERR))("q")

    def test_agent_timeout_falls_through_as_an_empty_answer(self, monkeypatch):
        assert self._replay(monkeypatch, CaseRun(answer="", error=AGENT_ERR))("q") == ""

    def test_swallowed_internal_error_is_scored_against_the_agent(self, monkeypatch):
        """An orchestrator-swallowed crash with no infra pattern is the agent's
        failure — old code raised ExecutorError and EXCLUDED it."""
        run = CaseRun(answer="I hit an internal error while working on that request. "
                             "Error: ValueError: boom")
        assert self._replay(monkeypatch, run)("q") == ""

    def test_clean_run_returns_its_answer(self, monkeypatch):
        assert self._replay(monkeypatch, CaseRun(answer="42"))("q") == "42"
