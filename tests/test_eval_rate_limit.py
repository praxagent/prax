"""Keyless tests for eval self-rate-limiting + retry (prax.eval.rate_limit).

No network, no keys — a fake replay_fn drives the retry/throttle logic; sleep is
monkeypatched so tests are instant.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def rl(monkeypatch):
    mod = importlib.reload(importlib.import_module("prax.eval.rate_limit"))
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)  # no real waits
    return mod


def test_transient_detection(rl):
    assert rl._looks_transient("") is True
    assert rl._looks_transient("   ") is True
    assert rl._looks_transient(None) is True
    assert rl._looks_transient("Connect timeout, please try again later.") is True
    assert rl._looks_transient("429 Too Many Requests") is True
    # A real answer is not transient...
    assert rl._looks_transient("Answer: B") is False
    # ...even a long one that discusses rate limits in prose (only short msgs scanned).
    long_ans = "The system uses a rate limit of 60 req/min. " * 20
    assert rl._looks_transient(long_ans) is False


def test_retries_empty_then_succeeds(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "4")
    calls = {"n": 0}

    def flaky(_prompt):
        calls["n"] += 1
        return "" if calls["n"] < 3 else "Answer: C"

    out = rl.call_with_rate_limit(flaky, "q")
    assert out == "Answer: C"
    assert calls["n"] == 3  # two empties retried, third succeeded


def test_retries_transient_string_then_succeeds(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "4")
    calls = {"n": 0}

    def flaky(_prompt):
        calls["n"] += 1
        return "Connect timeout, please try again later." if calls["n"] < 2 else "real answer"

    assert rl.call_with_rate_limit(flaky, "q") == "real answer"
    assert calls["n"] == 2


def test_retries_exception_then_succeeds(rl, monkeypatch):
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "3")
    calls = {"n": 0}

    def flaky(_prompt):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("connect timeout")
        return "ok"

    assert rl.call_with_rate_limit(flaky, "q") == "ok"


def test_exhausted_returns_last_response_not_raise(rl, monkeypatch):
    # All-empty: after retries it returns the (empty) response so the case still
    # scores as a normal miss rather than crashing the batch.
    monkeypatch.setenv("PRAX_EVAL_LLM_MAX_RETRIES", "2")
    calls = {"n": 0}

    def always_empty(_prompt):
        calls["n"] += 1
        return ""

    assert rl.call_with_rate_limit(always_empty, "q") == ""
    assert calls["n"] == 3  # 1 + 2 retries


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
