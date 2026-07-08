"""self_upgrade_tier is a transient session boost (in-memory), not a config write."""
from __future__ import annotations

import prax.agent.session_tier as st
from prax.agent import orchestrator as orch_mod


def _agent(monkeypatch, base="medium"):
    a = orch_mod.ConversationAgent.__new__(orch_mod.ConversationAgent)
    a._orchestrator_tier = base
    a._base_orchestrator_tier = base
    a._active_provider = "openai"
    a.tools = []
    a.llm = a.graph = object()

    class _CM:
        saver = None
    a.checkpoint_mgr = _CM()
    monkeypatch.setattr(orch_mod, "build_llm", lambda **k: object())
    monkeypatch.setattr(orch_mod, "build_agent_loop", lambda *a, **k: object())
    return a


def setup_function():
    st.clear_session_tier_floor()


def teardown_function():
    st.clear_session_tier_floor()


def test_self_upgrade_sets_floor_no_config_write(monkeypatch):
    called = {"cfg": False}
    import prax.plugins.llm_config as cfg
    monkeypatch.setattr(cfg, "update_component_config",
                        lambda *a, **k: called.__setitem__("cfg", True))
    from prax.agent.workspace_tools import self_upgrade_tier
    out = self_upgrade_tier.func("high")
    assert st.get_session_tier_floor() == "high"
    assert called["cfg"] is False  # no persistent config write
    assert "session" in out.lower()


def test_reset_applies_session_floor(monkeypatch):
    a = _agent(monkeypatch, base="medium")
    st.set_session_tier_floor("high")
    a._reset_tier_to_base()
    assert a._orchestrator_tier == "high"  # floor raises the effective base


def test_floor_below_base_is_ignored(monkeypatch):
    a = _agent(monkeypatch, base="high")
    st.set_session_tier_floor("medium")  # lower than base — must not downgrade
    a._reset_tier_to_base()
    assert a._orchestrator_tier == "high"


def test_no_floor_resets_to_shipped_base(monkeypatch):
    a = _agent(monkeypatch, base="medium")
    a._orchestrator_tier = "high"  # left elevated by a prior turn's auto-escalation
    a._reset_tier_to_base()
    assert a._orchestrator_tier == "medium"  # forgotten on the next turn
