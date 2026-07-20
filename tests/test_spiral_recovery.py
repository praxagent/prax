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
