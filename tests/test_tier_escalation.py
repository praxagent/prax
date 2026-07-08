"""Auto tier escalation: recursion thrash climbs low→medium→high, then fails."""
from __future__ import annotations

from prax.agent import orchestrator as orch_mod
from prax.agent.agent_loop import GraphRecursionError


def _agent(monkeypatch, base_tier="low", ceiling="high"):
    a = orch_mod.ConversationAgent.__new__(orch_mod.ConversationAgent)
    a._orchestrator_tier = base_tier
    a._base_orchestrator_tier = base_tier
    a._active_provider = "openai"
    a.tools = []
    a.llm = object()
    a.graph = object()

    class _CM:
        saver = None
        def can_retry(self, uid): return True
        def get_turn(self, uid): return None
        def graph_config(self, turn): return {}
    a.checkpoint_mgr = _CM()

    # Rebuilds are no-ops in the test — track tier transitions only.
    monkeypatch.setattr(orch_mod, "build_llm", lambda **k: object())
    monkeypatch.setattr(orch_mod, "build_agent_loop", lambda *a, **k: object())
    monkeypatch.setattr(orch_mod.settings, "auto_tier_escalation", True, raising=False)
    monkeypatch.setattr(orch_mod.settings, "auto_tier_escalation_ceiling", ceiling, raising=False)
    monkeypatch.setattr(a, "_drain_denylist_notice", lambda: "")
    return a


def test_escalates_low_to_medium_to_high_then_fails(monkeypatch):
    a = _agent(monkeypatch)
    seen = []

    def _once(messages, config, user_id, heartbeat):
        seen.append(a._orchestrator_tier)   # tier at each attempt
        raise GraphRecursionError("limit")

    monkeypatch.setattr(a, "_invoke_graph_once", _once)
    out = a._invoke_with_retry([], {"callbacks": []}, "u1", heartbeat=None)
    assert seen == ["low", "medium", "high"]      # climbed the ladder
    assert "got stuck" in out                     # graceful fail at ceiling
    assert a._orchestrator_tier == "high"


def test_escalation_succeeds_at_medium(monkeypatch):
    a = _agent(monkeypatch)
    attempts = {"n": 0}

    def _once(messages, config, user_id, heartbeat):
        attempts["n"] += 1
        if a._orchestrator_tier == "low":
            raise GraphRecursionError("limit")
        return "done at " + a._orchestrator_tier

    monkeypatch.setattr(a, "_invoke_graph_once", _once)
    out = a._invoke_with_retry([], {"callbacks": []}, "u1", heartbeat=None)
    assert out == "done at medium" and attempts["n"] == 2


def test_disabled_flag_fails_without_escalating(monkeypatch):
    a = _agent(monkeypatch)
    monkeypatch.setattr(orch_mod.settings, "auto_tier_escalation", False, raising=False)
    monkeypatch.setattr(a, "_invoke_graph_once",
                        lambda *A, **K: (_ for _ in ()).throw(GraphRecursionError("x")))
    out = a._invoke_with_retry([], {"callbacks": []}, "u1", heartbeat=None)
    assert "got stuck" in out and a._orchestrator_tier == "low"  # never escalated


def test_reset_returns_to_base(monkeypatch):
    a = _agent(monkeypatch)
    a._orchestrator_tier = "high"   # simulate a previously-escalated turn
    a._reset_tier_to_base()
    assert a._orchestrator_tier == "low"
