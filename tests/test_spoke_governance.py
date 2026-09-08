"""Spoke-layer governance.

Root cause 1 of the 2026-09-05 review: governance wrapped the hub's tool set,
which has ZERO HIGH-risk tools — every capability that can act lives in the
spokes, whose loops were built from raw tools.  Spoke build sites now hand
their loop ``govern_spoke_tools(...)`` output:

* ALWAYS recorded: an audit entry per call, and the lethal-trifecta legs the
  call touched (observability is unconditional);
* ENFORCED only under ``SPOKE_GOVERNANCE_ENABLED`` (default off): the HIGH
  first-call block / scoped confirm and the trifecta escalation.  Off, a spoke
  tool executes exactly as an unwrapped one — no budget accounting, no result
  tagging, no schema change, no loop/epistemic gates.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gt


@pytest.fixture(autouse=True)
def _fresh_turn():
    gt.begin_turn()
    yield
    gt.drain_audit_log()


def _flag(monkeypatch, value: bool) -> None:
    """Set SPOKE_GOVERNANCE_ENABLED on the LIVE settings object (the conftest
    reloads ``prax.settings`` per test, so a module-level import goes stale)."""
    from prax.settings import settings
    monkeypatch.setattr(settings, "spoke_governance_enabled", value)


def _high_tool(calls: list, name: str = "plugin_write"):
    """``plugin_write`` is HIGH in the central risk map."""
    return StructuredTool.from_function(
        func=lambda x="": calls.append(x) or f"ran:{x}",
        name=name, description="a HIGH-risk spoke tool",
    )


# ---------------------------------------------------------------------------
# The wrapper mode
# ---------------------------------------------------------------------------

class TestSpokeLayerMode:
    def test_flag_off_records_but_does_not_block(self, monkeypatch):
        _flag(monkeypatch, False)
        calls: list = []
        [governed] = gt.govern_spoke_tools([_high_tool(calls)])

        assert governed.invoke({"x": "first"}) == "ran:first"  # first call runs, untagged
        assert calls == ["first"]
        entries = gt.current_turn_state().audit
        assert len(entries) == 1
        assert entries[0]["tool_name"] == "plugin_write"
        assert entries[0]["risk"] == "high"
        assert "BLOCKED" not in (entries[0]["result"] or "")

    def test_flag_on_blocks_first_call(self, monkeypatch):
        _flag(monkeypatch, True)
        calls: list = []
        [governed] = gt.govern_spoke_tools([_high_tool(calls)])

        result = governed.invoke({"x": "first"})
        assert "HIGH risk" in result
        assert calls == []
        assert "BLOCKED" in gt.current_turn_state().audit[-1]["result"]
        # Confirmation semantics are the hub's: the second call executes.
        assert governed.invoke({"x": "first"}) == "ran:first"

    def test_enforcement_is_decided_at_build_time_from_the_setting(self, monkeypatch):
        _flag(monkeypatch, True)
        assert gt.spoke_governance_enforced() is True
        _flag(monkeypatch, False)
        assert gt.spoke_governance_enforced() is False

    def test_spoke_layer_does_not_touch_the_hub_budget_or_tag_or_reshape(self, monkeypatch):
        """With enforcement off a spoke tool behaves exactly as an unwrapped one."""
        _flag(monkeypatch, False)
        gt.init_turn_budget(1)  # a hub budget of one call
        raw = StructuredTool.from_function(
            func=lambda x="": f"page:{x}",
            name="fetch_url_content", description="INFORMATIONAL in the capability map",
        )
        [governed] = gt.govern_spoke_tools([raw])

        assert governed.invoke({"x": "a"}) == "page:a"  # no epistemic tag prepended
        assert governed.invoke({"x": "b"}) == "page:b"  # no budget block on call two
        assert gt.get_budget_status() == (0, 1)          # spoke calls don't count
        props = governed.args_schema.model_json_schema().get("properties") or {}
        assert "expected_observation" not in props       # schema unchanged
        assert set(props) == set(raw.args_schema.model_json_schema().get("properties") or {})

    def test_hub_layer_still_counts_budget_and_tags(self):
        """The equivalence half: the hub wrapper is unchanged by the spoke mode."""
        gt.init_turn_budget(1)
        raw = StructuredTool.from_function(
            func=lambda x="": f"page:{x}", name="fetch_url_content", description="fetch")
        governed = gt.wrap_with_governance(raw)
        assert governed.invoke({"x": "a"}).startswith("[INFORMATIONAL SOURCE")
        assert "budget exhausted" in governed.invoke({"x": "b"}).lower()
        props = governed.args_schema.model_json_schema().get("properties") or {}
        assert "expected_observation" in props

    def test_raw_tool_metadata_survives_the_binding_wrapper(self, monkeypatch):
        """``_risk_level`` lives on the raw tool; ``bind_tools_user_context``
        returns a fresh StructuredTool without it.  Governance goes on AFTER
        the binding, so it must classify from the raw tool."""
        from prax.agent.action_policy import RiskLevel
        _flag(monkeypatch, True)
        calls: list = []
        raw = StructuredTool.from_function(
            func=lambda x="": calls.append(x) or "ok",
            name="custom_tool_medium_by_name", description="unclassified → MEDIUM by name",
        )
        raw._risk_level = RiskLevel.HIGH
        [governed] = gt.govern_spoke_tools([raw])
        assert "HIGH risk" in governed.invoke({"x": "a"})
        assert calls == []
        assert governed._risk_level is RiskLevel.HIGH

    def test_trifecta_legs_recorded_always_escalated_only_when_enforced(self, monkeypatch):
        import prax.agent.trifecta as tf
        monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
        state = gt.current_turn_state()
        state.trifecta_untrusted = True
        state.trifecta_private = True
        sent: list = []
        sink = StructuredTool.from_function(
            func=lambda text="": sent.append(text) or "sent", name="send_sms", description="sink")

        _flag(monkeypatch, False)
        [observe_only] = gt.govern_spoke_tools([sink])
        assert observe_only.invoke({"text": "x"}) == "sent"  # not escalated: enforcement off
        assert sent == ["x"]
        assert state.audit[-1]["tool_name"] == "send_sms"

        _flag(monkeypatch, True)
        [enforced] = gt.govern_spoke_tools([sink])
        assert "trifecta" in enforced.invoke({"text": "y"}).lower()
        assert sent == ["x"]

    def test_spoke_reader_records_the_private_leg_without_enforcement(self, monkeypatch):
        _flag(monkeypatch, False)
        reader = StructuredTool.from_function(
            func=lambda x="": "the user's notes", name="memory_search", description="private")
        [governed] = gt.govern_spoke_tools([reader])
        governed.invoke({"x": "q"})
        assert gt.current_turn_state().trifecta_private is True

    def test_error_inside_a_spoke_tool_is_audited_and_re_raised(self, monkeypatch):
        _flag(monkeypatch, False)

        def _boom(x: str = "") -> str:
            raise ValueError("boom")

        [governed] = gt.govern_spoke_tools([
            StructuredTool.from_function(func=_boom, name="note_list", description="t")])
        with pytest.raises(ValueError, match="boom"):
            governed.invoke({"x": "a"})
        assert "ERROR" in gt.current_turn_state().audit[-1]["result"]

    def test_unknown_layer_is_rejected(self):
        with pytest.raises(ValueError):
            gt.wrap_with_governance(_high_tool([]), layer="hubb")


# ---------------------------------------------------------------------------
# Every spoke build site hands its loop governed tools
# ---------------------------------------------------------------------------

class _FakeGraph:
    def invoke(self, inputs, config=None):
        return {"messages": [AIMessage(content="done")]}


def _capture_loop(monkeypatch, module, captured: dict):
    """Patch a build site's loop/LLM construction so the tool list it builds
    can be inspected without a model or a network."""
    def _build_agent_loop(llm, tools, **kwargs):
        captured["tools"] = list(tools)
        return _FakeGraph()

    monkeypatch.setattr(module, "build_agent_loop", _build_agent_loop)
    monkeypatch.setattr(module, "build_llm", lambda **kwargs: object())


def _run_spoke_site(monkeypatch, tools):
    from prax.agent.spokes import _runner
    captured: dict = {}
    _capture_loop(monkeypatch, _runner, captured)
    _runner.run_spoke(task="t", system_prompt="s", tools=tools, config_key="subagent_test")
    return captured["tools"]


def _run_subagent_site(monkeypatch, tools):
    from prax.agent import subagent
    captured: dict = {}
    _capture_loop(monkeypatch, subagent, captured)
    monkeypatch.setattr(subagent, "_get_tools_for_category", lambda category: list(tools))
    subagent._run_subagent("t", "research")
    return captured["tools"]


def _research_site(monkeypatch, tools):
    from prax.agent import research_agent
    captured: dict = {}
    _capture_loop(monkeypatch, research_agent, captured)
    monkeypatch.setattr(research_agent, "_build_research_tools", lambda depth=0: list(tools))
    research_agent._run_research("q")
    return captured["tools"]


def _self_improve_site(monkeypatch, tools):
    from prax.agent import self_improve_agent
    captured: dict = {}
    _capture_loop(monkeypatch, self_improve_agent, captured)
    monkeypatch.setattr(self_improve_agent, "_build_self_improve_tools", lambda: list(tools))
    self_improve_agent.delegate_self_improve.func("t")
    return captured["tools"]


_SITES = {
    "run_spoke": _run_spoke_site,
    "_run_subagent": _run_subagent_site,
    "_run_research": _research_site,
    "delegate_self_improve": _self_improve_site,
}


@pytest.mark.parametrize("site", sorted(_SITES))
def test_build_site_hands_its_loop_governed_tools_flag_off(monkeypatch, site):
    _flag(monkeypatch, False)
    calls: list = []
    [governed] = _SITES[site](monkeypatch, [_high_tool(calls)])

    assert governed.name == "plugin_write"
    assert hasattr(governed, "_trifecta_legs")  # the governance wrapper stamps this
    assert governed.invoke({"x": "a"}) == "ran:a"  # flag off: runs on the first call
    assert calls == ["a"]
    audit = [e for e in gt.current_turn_state().audit if e["tool_name"] == "plugin_write"]
    assert len(audit) == 1 and "BLOCKED" not in (audit[0]["result"] or "")


@pytest.mark.parametrize("site", sorted(_SITES))
def test_build_site_hands_its_loop_governed_tools_flag_on(monkeypatch, site):
    _flag(monkeypatch, True)
    calls: list = []
    [governed] = _SITES[site](monkeypatch, [_high_tool(calls)])

    assert "HIGH risk" in governed.invoke({"x": "a"})  # flag on: blocked on the first call
    assert calls == []
    assert "BLOCKED" in gt.current_turn_state().audit[-1]["result"]


def test_build_site_still_binds_request_context(monkeypatch):
    """Governance replaced the bare ``bind_tools_user_context`` call — the
    binding must still be there underneath."""
    from prax.agent.user_context import current_user_id
    _flag(monkeypatch, False)
    whoami = StructuredTool.from_function(
        func=lambda: current_user_id.get() or "none", name="whoami", description="t")
    token = current_user_id.set("spoke-user")
    try:
        [governed] = _run_spoke_site(monkeypatch, [whoami])
    finally:
        current_user_id.reset(token)
    assert current_user_id.get() != "spoke-user"
    assert governed.invoke({}) == "spoke-user"
