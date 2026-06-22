"""Tests for M7 tool-boundary hardening: deny-by-default risk + scoped
HIGH-risk confirmation unlock."""
from __future__ import annotations

from langchain_core.tools import StructuredTool


def _make_tool(name: str):
    def func(x: str = "") -> str:
        return f"executed:{x}"
    return StructuredTool.from_function(func=func, name=name, description=f"test {name}")


def _reset():
    import prax.agent.governed_tool as gov
    gov._audit_buffer.clear()
    gov._high_risk_seen.clear()
    gov._high_risk_confirmed_tools.clear()
    gov._high_risk_confirmed = False
    gov._tool_call_count = 0
    gov._tool_call_budget = 0
    from prax.agent.loop_detector import reset as reset_loops
    reset_loops()


# --------------------------------------------------------------------------- #
# Deny-by-default risk classification
# --------------------------------------------------------------------------- #

class TestDenyByDefault:
    def test_unknown_tool_defaults_medium(self, monkeypatch):
        from prax.agent.action_policy import RiskLevel, get_risk_level
        from prax.settings import settings
        monkeypatch.setattr(settings, "unknown_tool_high_risk", False)
        assert get_risk_level("totally_unclassified_tool_xyz") == RiskLevel.MEDIUM

    def test_unknown_tool_high_when_enabled(self, monkeypatch):
        from prax.agent.action_policy import RiskLevel, get_risk_level
        from prax.settings import settings
        monkeypatch.setattr(settings, "unknown_tool_high_risk", True)
        assert get_risk_level("totally_unclassified_tool_xyz") == RiskLevel.HIGH

    def test_static_classification_unaffected_by_flag(self, monkeypatch):
        from prax.agent.action_policy import RiskLevel, get_risk_level
        from prax.settings import settings
        monkeypatch.setattr(settings, "unknown_tool_high_risk", True)
        # A statically-LOW tool stays LOW even with deny-by-default on.
        assert get_risk_level("get_current_datetime") == RiskLevel.LOW


# --------------------------------------------------------------------------- #
# Scoped HIGH-risk confirmation
# --------------------------------------------------------------------------- #

class TestScopedHighConfirm:
    def test_scoped_off_one_confirm_unlocks_all(self, monkeypatch):
        from prax.agent.governed_tool import wrap_with_governance
        from prax.settings import settings
        monkeypatch.setattr(settings, "high_risk_scoped_confirm", False)
        _reset()

        a = wrap_with_governance(_make_tool("plugin_write"))
        b = wrap_with_governance(_make_tool("schedule_create"))
        # Confirm tool A (call twice).
        assert "HIGH risk" in a.invoke({"x": "1"})
        assert "executed:1" in a.invoke({"x": "1"})
        # Default behaviour: tool B now runs on FIRST call (global unlock).
        assert "executed:2" in b.invoke({"x": "2"})

    def test_scoped_on_confirm_is_per_tool(self, monkeypatch):
        from prax.agent.governed_tool import wrap_with_governance
        from prax.settings import settings
        monkeypatch.setattr(settings, "high_risk_scoped_confirm", True)
        _reset()

        a = wrap_with_governance(_make_tool("plugin_write"))
        b = wrap_with_governance(_make_tool("schedule_create"))
        # Confirm tool A (call twice → executes).
        assert "HIGH risk" in a.invoke({"x": "1"})
        assert "executed:1" in a.invoke({"x": "1"})
        # Tool B must STILL require its own confirmation (not unlocked by A).
        assert "HIGH risk" in b.invoke({"x": "2"})
        # And executes on its own second call.
        assert "executed:2" in b.invoke({"x": "2"})
