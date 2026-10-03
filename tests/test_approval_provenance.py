"""Every executed call records why it was allowed to run.

Pattern credit: OpenWorker (Andrew Ng et al.) — approval provenance on every
tool call (user-approved, auto-approved, denied).
"""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gov
import prax.settings as prax_settings
from prax.agent import human_approval
from prax.agent.user_context import current_user_message


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "hard_floors_enabled", False)
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", False)
    gov.begin_turn()
    gov.drain_audit_log()
    yield
    gov.drain_audit_log()


def _tool(name, *, enforce=True):
    return gov.wrap_with_governance(
        StructuredTool.from_function(func=lambda x="": "ok", name=name, description="t"),
        layer="spoke", enforce=enforce)


def _executed():
    return [e for e in gov.drain_audit_log() if e.get("result") == "ok"]


def test_a_non_high_call_needed_none():
    _tool("browser_navigate").invoke({"x": "1"})
    (entry,) = _executed()
    assert entry["approval"] == "none_needed"


def test_the_model_confirming_itself_is_recorded_as_such():
    t = _tool("plugin_write")
    assert t.invoke({"x": "1"}).startswith("⚠️")
    assert t.invoke({"x": "1"}) == "ok"
    (entry,) = _executed()
    assert entry["approval"] == "model_reconfirmed"


def test_a_later_high_call_unlocked_by_an_earlier_one():
    a, b = _tool("plugin_write"), _tool("plugin_activate")
    a.invoke({"x": "1"})
    a.invoke({"x": "1"})
    assert b.invoke({"x": "2"}) == "ok"  # turn-wide latch (unscoped default)
    assert [e["approval"] for e in _executed()] == ["model_reconfirmed", "earlier_confirmation"]


def test_high_risk_in_an_unenforced_spoke_is_visible():
    _tool("plugin_write", enforce=False).invoke({"x": "1"})
    (entry,) = _executed()
    assert entry["approval"] == "high_risk_not_enforced"


def test_a_persons_approval_carries_its_id(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "out_of_band_approvals_enabled", True)
    monkeypatch.setattr(human_approval, "request",
                        lambda *a, **k: human_approval.Decision(True, "", "ap9", "tj"))
    assert _tool("plugin_write").invoke({"x": "1"}) == "ok"
    (entry,) = _executed()
    assert entry["approval"] == "person:ap9"


def test_the_users_own_message_for_a_floor(monkeypatch):
    monkeypatch.setattr(prax_settings.settings, "hard_floors_enabled", True)
    token = current_user_message.set("install the weather plugin")
    try:
        t = _tool("plugin_import")
        assert t.invoke({"x": "weather"}) != "ok"  # no 'name' kwarg -> no target -> refused
        t2 = gov.wrap_with_governance(
            StructuredTool.from_function(func=lambda name="": "ok", name="plugin_import", description="t"),
            layer="spoke", enforce=True)
        assert t2.invoke({"name": "weather"}) == "ok"
    finally:
        current_user_message.reset(token)
    (entry,) = _executed()
    assert entry["approval"] == "user_message"


def test_smart_auto_approve(monkeypatch):
    monkeypatch.setattr(gov, "_user_explicitly_requested_action", lambda name: True)
    assert _tool("browser_click").invoke({"x": "1"}) == "ok"
    (entry,) = _executed()
    assert entry["approval"] == "auto:user_request"


def test_earned_trust(monkeypatch):
    from prax.agent import earned_trust

    monkeypatch.setattr(earned_trust, "get_trust_adjustments", lambda c: earned_trust.TrustAdjustments(
        risk_downgrade_eligible={"browser_click"}))
    assert _tool("browser_click").invoke({"x": "1"}) == "ok"
    (entry,) = _executed()
    assert entry["approval"] == "auto:earned_trust"
