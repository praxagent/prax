"""Keyless tests for eval self-rate-limiting + retry (prax.eval.rate_limit).

No network, no keys — a fake replay_fn drives the retry/throttle logic; sleep is
monkeypatched so tests are instant.

The load-bearing invariant: only a TRANSPORT failure (an exception) is retried.
A returned answer — empty, blank, or a short "transient-looking" string — is the
agent's attempt and is returned as-is, because the summary labels the protocol
``pass@1`` and a content-triggered re-run would have been a silent pass@k.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def rl(monkeypatch):
    mod = importlib.reload(importlib.import_module("prax.eval.rate_limit"))
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)  # no real waits
    return mod


@pytest.mark.parametrize("first_answer", [
    "",                                            # empty
    "   ",                                         # blank
    None,                                          # no content at all
    "Connect timeout, please try again later.",    # provider error AS the answer
    "429 Too Many Requests",
])
def test_answer_content_never_triggers_a_retry(rl, monkeypatch, first_answer):
    """pass@1 means pass@1: whatever the first answer looks like, it is the
    attempt that gets scored. A second call here would be a second attempt at
    the same case, selected on the content of the first."""
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "4")
    calls = {"n": 0}

    def would_succeed_on_retry(_prompt):
        calls["n"] += 1
        return first_answer if calls["n"] == 1 else "Answer: C"

    assert rl.call_with_rate_limit(would_succeed_on_retry, "q") == first_answer
    assert calls["n"] == 1


def test_transient_markers_still_classify_failure_reasons(rl):
    # The marker list survives for classify_transient — it decides whether an
    # EXCEPTION's reason is worth retrying, never whether an answer is.
    assert rl.classify_transient("Connect timeout, please try again later.") is True


def test_retries_exception_then_succeeds(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "3")
    calls = {"n": 0}

    def flaky(_prompt):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("connect timeout")
        return "ok"

    assert rl.call_with_rate_limit(flaky, "q") == "ok"


def test_empty_answer_is_returned_once_not_raised(rl, monkeypatch):
    # An empty answer is returned (the case scores as a normal miss, the batch
    # keeps going) — and it is NOT re-attempted.
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "2")
    calls = {"n": 0}

    def always_empty(_prompt):
        calls["n"] += 1
        return ""

    assert rl.call_with_rate_limit(always_empty, "q") == ""
    assert calls["n"] == 1


def test_exhausted_all_exceptions_reraises(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "2")

    def always_raise(_prompt):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        rl.call_with_rate_limit(always_raise, "q")


def test_no_retry_disabled(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "0")
    calls = {"n": 0}

    def flaky(_prompt):
        calls["n"] += 1
        return ""

    assert rl.call_with_rate_limit(flaky, "q") == ""
    assert calls["n"] == 1  # no retries


def test_good_answer_not_retried(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "4")
    calls = {"n": 0}

    def good(_prompt):
        calls["n"] += 1
        return "Answer: A"

    assert rl.call_with_rate_limit(good, "q") == "Answer: A"
    assert calls["n"] == 1  # a real answer is returned immediately


def test_throttle_spaces_calls(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MIN_INTERVAL_S", "5")
    slept = []
    monkeypatch.setattr(rl.time, "sleep", lambda s: slept.append(s))
    # Fake monotonic so the second call sees ~0 elapsed and must wait ~5s.
    t = {"v": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: t["v"])
    rl.call_with_rate_limit(lambda _p: "x", "q1")
    rl.call_with_rate_limit(lambda _p: "y", "q2")
    assert any(abs(s - 5.0) < 0.01 for s in slept)  # throttled ~5s


def test_classify_transient(rl):
    # Permanent auth/config failures are NOT retryable.
    assert rl.classify_transient("AuthenticationError: 401 - Missing Authentication header") is False
    assert rl.classify_transient("403 Forbidden") is False
    assert rl.classify_transient("insufficient_quota") is False
    # Flaky infra IS retryable.
    assert rl.classify_transient("Connect timeout") is True
    assert rl.classify_transient("429 Too Many Requests") is True
    assert rl.classify_transient("503 service unavailable") is True


def test_permanent_executor_error_is_not_retried(rl):
    calls = {"n": 0}

    def always_401(_prompt):
        calls["n"] += 1
        raise rl.ExecutorError("401 Missing Authentication header", transient=False)

    with pytest.raises(rl.ExecutorError):
        rl.call_with_rate_limit(always_401, "q")
    assert calls["n"] == 1  # a bad key is surfaced at once — no wasted retries


def test_transient_executor_error_is_retried(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "3")
    calls = {"n": 0}

    def flaky(_prompt):
        calls["n"] += 1
        if calls["n"] < 3:
            raise rl.ExecutorError("Connect timeout", transient=True)
        return "Answer: A"

    assert rl.call_with_rate_limit(flaky, "q") == "Answer: A"
    assert calls["n"] == 3  # retried through the transient blips
