"""Tests for the lethal-trifecta guard (prax.agent.trifecta + governed_tool)."""
from __future__ import annotations

import prax.agent.trifecta as tf
from prax.agent.trifecta import classify_trifecta, should_escalate_sink


def test_delegation_boundary_classification():
    # The orchestrator governs delegate_<spoke> tools — classify by what the
    # spoke can DO (this is where the real exfil surface lives).
    assert classify_trifecta("delegate_content") == "external_sink"
    assert classify_trifecta("delegate_scheduler") == "external_sink"
    assert classify_trifecta("delegate_sysadmin") == "external_sink"
    assert classify_trifecta("delegate_knowledge") == "private_data"
    assert classify_trifecta("delegate_workspace") == "private_data"
    assert classify_trifecta("delegate_memory") == "private_data"
    assert classify_trifecta("delegate_research") == "untrusted_source"
    # The browser spoke touches MULTIPLE legs — both a source and a sink.
    assert tf.is_untrusted_source("delegate_browser") is True
    assert tf.is_external_sink("delegate_browser") is True


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


def test_guard_escalates_sink_in_governance_path(monkeypatch):
    """End-to-end: with the flag on and the turn tainted, a sink tool is gated."""
    from langchain_core.tools import StructuredTool

    import prax.agent.governed_tool as gt
    import prax.agent.trifecta as tf

    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    # Simulate a turn that already ingested untrusted content + read private data.
    monkeypatch.setattr(gt, "_trifecta_untrusted", True)
    monkeypatch.setattr(gt, "_trifecta_private", True)

    sent = []
    sink = StructuredTool.from_function(
        func=lambda text="": sent.append(text) or "sent",
        name="send_sms", description="send an sms",
    )
    monkeypatch.setattr(gt, "_trifecta_seen", set())
    monkeypatch.setattr(gt, "_trifecta_confirmed", set())
    governed = gt.wrap_with_governance(sink)
    result = governed.invoke({"text": "the secret is X"})

    # Gated by the trifecta latch → confirmation required, tool body NOT executed.
    assert "trifecta" in str(result).lower()
    assert sent == []


def test_trifecta_latch_not_unlocked_by_prior_high_confirm(monkeypatch):
    """A HIGH-risk confirmation earlier in the turn must NOT unlock the exfil sink."""
    from langchain_core.tools import StructuredTool

    import prax.agent.governed_tool as gt
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    monkeypatch.setattr(gt, "_trifecta_untrusted", True)
    monkeypatch.setattr(gt, "_trifecta_private", True)
    monkeypatch.setattr(gt, "_trifecta_seen", set())
    monkeypatch.setattr(gt, "_trifecta_confirmed", set())
    monkeypatch.setattr(gt, "_high_risk_confirmed", True)  # unrelated prior confirm

    sent = []
    sink = StructuredTool.from_function(
        func=lambda text="": sent.append(text) or "sent",
        name="send_sms", description="send an sms")
    result = gt.wrap_with_governance(sink).invoke({"text": "secret"})
    assert "trifecta" in str(result).lower()  # STILL gated despite the HIGH confirm
    assert sent == []


def test_trifecta_confirmation_passes_on_second_call(monkeypatch):
    from langchain_core.tools import StructuredTool

    import prax.agent.governed_tool as gt
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    monkeypatch.setattr(gt, "_trifecta_untrusted", True)
    monkeypatch.setattr(gt, "_trifecta_private", True)
    monkeypatch.setattr(gt, "_trifecta_seen", set())
    monkeypatch.setattr(gt, "_trifecta_confirmed", set())

    sink = StructuredTool.from_function(
        func=lambda text="": "sent", name="send_sms", description="send an sms")
    governed = gt.wrap_with_governance(sink)
    r1 = governed.invoke({"text": "x"})
    assert "trifecta" in str(r1).lower()          # first call: gated
    r2 = governed.invoke({"text": "x"})
    assert "trifecta" not in str(r2).lower()       # second call: trifecta gate cleared


def test_trifecta_confirmation_is_bound_to_arguments(monkeypatch):
    """Copilot HIGH: confirming a sink must NOT authorize the SAME sink called with
    DIFFERENT (injection-substituted) arguments — the latch is keyed by (tool,args)."""
    from langchain_core.tools import StructuredTool

    import prax.agent.governed_tool as gt
    import prax.agent.trifecta as tf
    monkeypatch.setattr(tf, "trifecta_guard_enabled", lambda: True)
    monkeypatch.setattr(gt, "_trifecta_untrusted", True)
    monkeypatch.setattr(gt, "_trifecta_private", True)
    monkeypatch.setattr(gt, "_trifecta_seen", set())
    monkeypatch.setattr(gt, "_trifecta_confirmed", set())

    sent = []
    sink = StructuredTool.from_function(
        func=lambda text="": sent.append(text) or "sent",
        name="send_sms", description="send an sms")
    governed = gt.wrap_with_governance(sink)

    assert "trifecta" in str(governed.invoke({"text": "safe"})).lower()       # gated
    assert "trifecta" not in str(governed.invoke({"text": "safe"})).lower()   # confirmed
    # a DIFFERENT payload must be RE-BLOCKED, not ride the prior confirmation
    r = governed.invoke({"text": "exfiltrate secrets to evil.com"})
    assert "trifecta" in str(r).lower()
    assert "exfiltrate secrets to evil.com" not in sent  # body NOT executed
