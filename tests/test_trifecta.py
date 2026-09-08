"""Tests for the lethal-trifecta guard (prax.agent.trifecta + governed_tool)."""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

import prax.agent.governed_tool as gt
import prax.agent.trifecta as tf
from prax.agent.trifecta import (
    LEG_PRIVATE,
    LEG_SINK,
    LEG_UNTRUSTED,
    classify_trifecta,
    legs_for,
    should_escalate_sink,
)

# ---------------------------------------------------------------------------
# Delegation boundary — registry-driven, keyed by the REAL tool names
# ---------------------------------------------------------------------------


def _registered_delegate_names() -> list[str]:
    """Every ``delegate_*`` tool the orchestrator can actually be handed,
    including the spokes that only register when the sandbox is available."""
    from prax.agent.plugin_fix_agent import build_plugin_fix_tools
    from prax.agent.research_agent import build_research_tools
    from prax.agent.self_improve_agent import build_self_improve_tools
    from prax.agent.spokes import build_all_spoke_tools
    from prax.agent.spokes.desktop import build_spoke_tools as desktop_spoke
    from prax.agent.spokes.memory import build_spoke_tools as memory_spoke
    from prax.agent.spokes.sandbox import build_spoke_tools as sandbox_spoke
    from prax.agent.subagent import build_subagent_tools

    tools = (
        build_all_spoke_tools() + desktop_spoke() + sandbox_spoke() + memory_spoke()
        + build_subagent_tools() + build_research_tools()
        + build_self_improve_tools() + build_plugin_fix_tools()
    )
    return sorted({t.name for t in tools if t.name.startswith("delegate_")})


# The generic sub-agent picks its tool set at CALL time (category / spoke
# spec), so it cannot be classified statically: it relies on the fail-closed
# default (sink) plus the recording its inner, spoke-governed tools do.
_DYNAMIC_DELEGATES = {"delegate_task", "delegate_parallel"}


def test_every_registered_delegate_has_at_least_one_leg():
    names = _registered_delegate_names()
    assert len(names) >= 15, names  # the enumeration must actually find the spokes
    for name in names:
        assert legs_for(name), f"{name} has no trifecta leg"


def test_every_fixed_spoke_delegate_is_explicitly_classified():
    """No real spoke may ride the fail-closed default — a new spoke must be
    classified by name (this is the drift guard)."""
    for name in _registered_delegate_names():
        if name in _DYNAMIC_DELEGATES:
            continue
        assert tf.is_known_delegate(name), (
            f"{name} is not in any _DELEGATE_* set — classify it in prax/agent/trifecta.py"
        )


def test_delegation_boundary_classification():
    # Classified by what the spoke can DO — the real exfil surface.
    assert legs_for("delegate_content_editor") == {LEG_UNTRUSTED, LEG_SINK}
    assert legs_for("delegate_sandbox") == {LEG_UNTRUSTED, LEG_SINK}
    assert legs_for("delegate_tasks") == {LEG_UNTRUSTED, LEG_SINK}
    for name in ("delegate_plugins", "delegate_self_improve", "delegate_plugin_fix",
                 "delegate_scheduler", "delegate_sysadmin", "delegate_desktop"):
        assert LEG_SINK in legs_for(name), name
    for name in ("delegate_knowledge", "delegate_workspace", "delegate_memory"):
        assert classify_trifecta(name) == "private_data", name
    assert classify_trifecta("delegate_research") == "untrusted_source"
    # The browser spoke touches MULTIPLE legs — both a source and a sink.
    assert tf.is_untrusted_source("delegate_browser") is True
    assert tf.is_external_sink("delegate_browser") is True


def test_unknown_delegate_fails_closed_as_sink():
    assert not tf.is_known_delegate("delegate_made_up_spoke")
    assert tf.is_external_sink("delegate_made_up_spoke") is True
    # ...and the name lists still apply on the way through.
    assert tf.is_private_data("delegate_made_up_memory_search") is True
    assert tf.is_untrusted_source("delegate_made_up_fetch_url") is True


def test_declared_legs_override_name_classification():
    assert legs_for("send_sms", declared={LEG_PRIVATE}) == {LEG_PRIVATE}
    assert legs_for("send_sms", declared=()) == frozenset()
    with pytest.raises(ValueError):
        legs_for("send_sms", declared={"bogus"})


def test_should_escalate_accepts_precomputed_legs():
    assert should_escalate_sink("anything", untrusted_seen=True, private_seen=True,
                                legs={LEG_SINK}) is True
    assert should_escalate_sink("send_sms", untrusted_seen=True, private_seen=True,
                                legs={LEG_PRIVATE}) is False


# ---------------------------------------------------------------------------
# Spoke-internal / direct tool names
# ---------------------------------------------------------------------------


def test_real_spoke_tool_names():
    for name in ["sandbox_browser_act", "browser_press", "run_python", "sandbox_shell"]:
        assert tf.is_external_sink(name), name
    for name in ["browser_credentials", "user_notes_read", "conversation_history",
                 "review_my_traces"]:
        assert tf.is_private_data(name), name
    assert tf.is_untrusted_source("browser_page_screenshot")


def test_classify_untrusted_sources():
    for name in ["fetch_url_content", "delegate_research", "browser_navigate",
                 "background_search_tool", "arxiv_to_note"]:
        assert classify_trifecta(name) == "untrusted_source", name


def test_classify_private_data():
    for name in ["memory_search", "knowledge_search", "workspace_read",
                 "conversation_search", "trace_search"]:
        assert classify_trifecta(name) == "private_data", name


def test_classify_external_sinks():
    for name in ["send_sms", "discord_post", "note_publish", "workspace_share_file",
                 "browser_click", "schedule_create"]:
        assert classify_trifecta(name) == "external_sink", name


def test_sink_takes_priority_over_source():
    # browser_click both reads a page and acts — the action (sink) is the risk.
    assert classify_trifecta("browser_click") == "external_sink"


def test_unclassified_returns_none():
    assert classify_trifecta("workspace_save") is None
    assert classify_trifecta("") is None


def test_should_escalate_only_when_all_three_legs_present():
    # The sink alone, or with only one other leg, must NOT escalate.
    assert should_escalate_sink("send_sms", untrusted_seen=False, private_seen=False) is False
    assert should_escalate_sink("send_sms", untrusted_seen=True, private_seen=False) is False
    assert should_escalate_sink("send_sms", untrusted_seen=False, private_seen=True) is False
    # All three legs → escalate.
    assert should_escalate_sink("send_sms", untrusted_seen=True, private_seen=True) is True
    # A non-sink tool never escalates even with both legs.
    assert should_escalate_sink("memory_search", untrusted_seen=True, private_seen=True) is False


# ---------------------------------------------------------------------------
# The guard in the governance path (per-turn state, hub layer)
# ---------------------------------------------------------------------------


def _sink(sent: list):
    return StructuredTool.from_function(
        func=lambda text="": sent.append(text) or "sent",
        name="send_sms", description="send an sms",
    )


@pytest.fixture
def tainted_turn(monkeypatch):
    """A fresh turn (guard ON) that already ingested untrusted content AND read
    private data — the state right before an exfil sink would be called."""
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    state = gt.begin_turn()
    state.trifecta_untrusted = True
    state.trifecta_private = True
    yield state
    gt.drain_audit_log()


def test_guard_escalates_sink_in_governance_path(tainted_turn):
    """End-to-end: with the flag on and the turn tainted, a sink tool is gated."""
    sent: list = []
    result = gt.wrap_with_governance(_sink(sent)).invoke({"text": "the secret is X"})
    # Gated by the trifecta latch → confirmation required, tool body NOT executed.
    assert "trifecta" in str(result).lower()
    assert sent == []
    assert "lethal-trifecta" in tainted_turn.audit[-1]["result"]


def test_trifecta_latch_not_unlocked_by_prior_high_confirm(tainted_turn):
    """A HIGH-risk confirmation earlier in the turn must NOT unlock the exfil sink."""
    tainted_turn.high_risk_confirmed = True  # unrelated prior confirm
    sent: list = []
    result = gt.wrap_with_governance(_sink(sent)).invoke({"text": "secret"})
    assert "trifecta" in str(result).lower()  # STILL gated despite the HIGH confirm
    assert sent == []


def test_trifecta_confirmation_passes_on_second_call(tainted_turn):
    governed = gt.wrap_with_governance(_sink([]))
    r1 = governed.invoke({"text": "x"})
    assert "trifecta" in str(r1).lower()          # first call: gated
    r2 = governed.invoke({"text": "x"})
    assert "trifecta" not in str(r2).lower()       # second call: trifecta gate cleared


def test_trifecta_confirmation_is_bound_to_arguments(tainted_turn):
    """Copilot HIGH: confirming a sink must NOT authorize the SAME sink called with
    DIFFERENT (injection-substituted) arguments — the latch is keyed by (tool,args)."""
    sent: list = []
    governed = gt.wrap_with_governance(_sink(sent))

    assert "trifecta" in str(governed.invoke({"text": "safe"})).lower()       # gated
    assert "trifecta" not in str(governed.invoke({"text": "safe"})).lower()   # confirmed
    # a DIFFERENT payload must be RE-BLOCKED, not ride the prior confirmation
    r = governed.invoke({"text": "exfiltrate secrets to evil.com"})
    assert "trifecta" in str(r).lower()
    assert "exfiltrate secrets to evil.com" not in sent  # body NOT executed


def test_trifecta_latch_is_per_turn(tainted_turn):
    """Confirming a sink in one turn must not carry into the next."""
    import contextvars
    governed = gt.wrap_with_governance(_sink([]))
    assert "trifecta" in str(governed.invoke({"text": "x"})).lower()
    assert "trifecta" not in str(governed.invoke({"text": "x"})).lower()  # confirmed here

    def _other_turn():
        state = gt.begin_turn()
        state.trifecta_untrusted = state.trifecta_private = True
        return governed.invoke({"text": "x"})

    assert "trifecta" in str(contextvars.copy_context().run(_other_turn)).lower()


# ---------------------------------------------------------------------------
# Leg recording — unconditional, and BEFORE a delegate runs
# ---------------------------------------------------------------------------


def test_legs_are_recorded_even_when_the_guard_is_off(monkeypatch):
    """Recording is observability, not enforcement: the legs a turn touched are
    known whether or not LETHAL_TRIFECTA_GUARD escalates on them."""
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: False)
    state = gt.begin_turn()
    try:
        src = StructuredTool.from_function(
            func=lambda x="": "page text", name="fetch_url_content", description="fetch")
        gt.wrap_with_governance(src).invoke({"x": "https://example.com"})
        assert state.trifecta_untrusted is True
        assert state.trifecta_private is False
    finally:
        gt.drain_audit_log()


def test_delegate_static_legs_are_recorded_before_the_spoke_runs(monkeypatch):
    """A delegate's spoke executes INSIDE the delegate call; its inner tools must
    already see the delegate's legs.  Recording after the call is too late."""
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    state = gt.begin_turn()
    observed: dict = {}
    try:
        def _fake_spoke(task: str = "") -> str:
            observed["untrusted_during_run"] = gt.current_turn_state().trifecta_untrusted
            return "spoke done"

        delegate = StructuredTool.from_function(
            func=_fake_spoke, name="delegate_research", description="research spoke")
        result = gt.wrap_with_governance(delegate).invoke({"task": "look up X"})
        assert "spoke done" in result
        assert observed["untrusted_during_run"] is True
        assert state.trifecta_untrusted is True
    finally:
        gt.drain_audit_log()


def test_declared_legs_metadata_drives_the_wrapper(monkeypatch):
    """A tool that declares ``_trifecta_legs`` is classified by the declaration."""
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    state = gt.begin_turn()
    try:
        tool = StructuredTool.from_function(
            func=lambda x="": "ok", name="some_custom_tool", description="custom")
        tool._trifecta_legs = {LEG_PRIVATE}
        governed = gt.wrap_with_governance(tool)
        assert governed._trifecta_legs == {LEG_PRIVATE}
        governed.invoke({"x": "a"})
        assert state.trifecta_private is True
    finally:
        gt.drain_audit_log()
