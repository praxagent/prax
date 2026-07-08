"""GraphRecursionError must fail gracefully (honest message, no retry burn)."""
from __future__ import annotations

from prax.agent.agent_loop import GraphRecursionError


def test_recursion_returns_graceful_message(monkeypatch):
    from prax.agent import orchestrator as orch_mod

    orch = orch_mod.ConversationAgent.__new__(orch_mod.ConversationAgent)

    calls = {"n": 0}

    def _once(messages, config, user_id, heartbeat):
        calls["n"] += 1
        raise GraphRecursionError("Recursion limit of 50 reached")

    monkeypatch.setattr(orch, "_invoke_graph_once", _once)
    monkeypatch.setattr(orch, "_drain_denylist_notice", lambda: "")
    # checkpoint_mgr.can_retry would allow retries; ensure we DON'T reach it.
    class _CM:
        def can_retry(self, uid): raise AssertionError("must not retry on recursion")
    orch.checkpoint_mgr = _CM()

    out = orch._invoke_with_retry([], {}, "u1", heartbeat=None)
    assert "stuck repeating" in out
    assert calls["n"] == 1  # invoked once, no retry loop
