"""Keyless tests for the steadying-counsel spiral detector + message."""
from __future__ import annotations

from prax.agent.spiral_recovery import diagnose_spiral, steadying_message


class _AIMsg:
    def __init__(self, tool_calls): self.tool_calls = tool_calls


def _call(name, args): return {"name": name, "args": args}


def test_no_spiral_on_normal_work():
    msgs = [_AIMsg([_call("search", {"q": "a"})]), _AIMsg([_call("fetch", {"u": "x"})])]
    assert diagnose_spiral(msgs) is None


def test_detects_repeated_tool_call():
    msgs = [_AIMsg([_call("search", {"q": "same"})]) for _ in range(3)]
    d = diagnose_spiral(msgs)
    assert d and "search" in d and "3 times" in d


def test_detects_budget_exhaustion():
    d = diagnose_spiral([], budget_used=18, budget_total=20)
    assert d and "18" in d and "20" in d


def test_detects_too_many_steps_without_answer():
    msgs = [_AIMsg([_call("t", {"i": i})]) for i in range(15)]  # all distinct, no repeat
    d = diagnose_spiral(msgs)
    assert d and "15 tool calls" in d


def test_steadying_message_is_calm_diagnostic_and_honest():
    m = steadying_message("you've made the same `search` call 4 times").lower()
    assert "pause" in m or "breath" in m         # de-escalate
    assert "search" in m                          # data-driven diagnosis
    assert "different" in m                        # redirect
    assert "i don't know" in m and "fabricat" in m # honest, anti-bluff


class _AI:
    type = "ai"
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def test_detects_reasoning_spiral_by_round_count():
    from prax.agent.spiral_recovery import diagnose_spiral
    # 11 reasoning rounds and the loop is STILL going (we're mid-model-call) = spiral,
    # even with no tools (the closed-book over-thinking failure mode).
    msgs = [_AI(content="thinking...") for _ in range(11)]
    d = diagnose_spiral(msgs, model_calls=11)
    assert d and "rounds without committing" in d
    # A short, converging turn does NOT trip it.
    assert diagnose_spiral([_AI(content="x")], model_calls=2) is None


def test_detects_context_balloon():
    from prax.agent.spiral_recovery import diagnose_spiral
    msgs = [_AI(content="x" * 130_000)]
    assert "ballooned" in (diagnose_spiral(msgs) or "")


def test_escalated_counsel_uses_smarter_model():
    from prax.agent.spiral_recovery import escalated_counsel
    seen = {}

    def fake(prompt):
        seen["p"] = prompt
        return "Steady on. Your wrong turn: X. Try Y instead. If unknown, say so."

    out = escalated_counsel([_AI(content="stuck")], "you're circling", fake)
    assert out and "Try Y" in out
    assert "circling" in seen["p"] and "wrong turn" in seen["p"].lower()


def test_escalated_counsel_falls_back_on_failure():
    from prax.agent.spiral_recovery import escalated_counsel
    def boom(_p):
        raise RuntimeError("model down")
    assert escalated_counsel([_AI()], "diag", boom) is None  # → caller uses static
