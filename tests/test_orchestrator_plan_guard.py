"""Tests for the orchestrator's _auto_complete_plan_steps caveat guard.

Regression coverage for the ROP-note failure: the orchestrator's
auto-completer marked every plan step done after a delegation returned
without error keywords, even when the sub-agent's reply explicitly said
"it does not guarantee the synthesized summary/diagram format you asked
for. If you want, I can...". The main agent then lied "Done" to the
user.

The guard refuses to auto-complete when any caveat marker is present in
a delegation reply. Prax must explicitly call ``agent_step_done`` for
each step he actually completed.
"""
from __future__ import annotations

import time

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import prax.agent.orchestrator as orchestrator_module
from prax.agent.orchestrator import ConversationAgent
from prax.services import workspace_service


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_service.settings, "workspace_dir", str(tmp_path))
    return tmp_path


USER = "+15550002222"


def _delegation(name: str, content: str) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=f"c_{name}")


class TestCaveatGuard:
    def test_response_has_caveat_detects_one_caveat(self):
        content = "Saved and readable. One caveat: I archived the page but..."
        assert ConversationAgent._response_has_caveat(content) == "one caveat"

    def test_response_has_caveat_detects_does_not_guarantee(self):
        content = (
            "Saved the note, but it does not guarantee the synthesized summary "
            "format you asked for."
        )
        marker = ConversationAgent._response_has_caveat(content)
        assert marker in {"does not guarantee", "but it does not guarantee"}

    def test_response_has_caveat_detects_if_you_want(self):
        content = (
            "Archived the page. If you want, I can now turn that into a "
            "proper explainer note."
        )
        assert ConversationAgent._response_has_caveat(content) == "if you want, i can"

    def test_response_has_caveat_returns_none_for_clean(self):
        content = "Done. Saved the note with a full synthesized summary and a mermaid diagram."
        assert ConversationAgent._response_has_caveat(content) is None

    def test_response_has_caveat_empty(self):
        assert ConversationAgent._response_has_caveat("") is None
        assert ConversationAgent._response_has_caveat(None) is None  # type: ignore[arg-type]


class TestUrlRecoveryGuard:
    def test_continues_when_url_failure_asks_user_for_next_step(self):
        messages = [
            ToolMessage(
                content="Reader returned HTTP 400 for https://example.com/post",
                name="fetch_url_content",
                tool_call_id="c_reader",
            ),
            AIMessage(content="I couldn't read it. If you want, I can try the browser."),
        ]
        assert ConversationAgent._should_continue_after_url_failure(
            "Create a note from https://example.com/post",
            messages,
        )

    def test_stops_after_reader_browser_and_search_all_tried(self):
        messages = [
            ToolMessage(
                content="Reader returned HTTP 400 for https://example.com/post",
                name="fetch_url_content",
                tool_call_id="c_reader",
            ),
            ToolMessage(
                content="ERR_NAME_NOT_RESOLVED",
                name="delegate_browser",
                tool_call_id="c_browser",
            ),
            ToolMessage(
                content="No matching mirror found",
                name="background_search_tool",
                tool_call_id="c_search",
            ),
            AIMessage(content="I couldn't read it. If you want, paste the content."),
        ]
        assert not ConversationAgent._should_continue_after_url_failure(
            "Create a note from https://example.com/post",
            messages,
        )


class TestGraphInvokeTimeout:
    def test_invoke_graph_once_returns_after_timeout(self, monkeypatch):
        class SlowGraph:
            def invoke(self, payload, config=None):
                time.sleep(2)
                return {"messages": []}

        agent = object.__new__(ConversationAgent)
        agent.graph = SlowGraph()
        monkeypatch.setattr(orchestrator_module.settings, "agent_run_timeout", 0.05)

        started = time.monotonic()
        with pytest.raises(TimeoutError):
            agent._invoke_graph_once([], {}, USER)

        assert time.monotonic() - started < 0.5


class TestAutoCompleteGuard:
    """Integration-style tests for _auto_complete_plan_steps using a real
    plan file in a temporary workspace."""

    def _make_plan(self, ws):
        workspace_service.create_plan(
            USER,
            "Create a deep-dive note on ROP chaining",
            [
                "Log and fetch the URL content",
                "Read and synthesize the article into a concise note",
                "Create the knowledge note and verify the URL",
            ],
        )

    def test_skips_completion_when_caveat_marker_present(self, ws):
        """The exact ROP-note regression: caveated delegation reply must
        NOT cause all plan steps to be auto-marked done."""
        self._make_plan(ws)

        caveat_reply = (
            "Saved and readable.\n\nNote URL: https://example/notes/x\n\n"
            "One caveat: I archived the page as a note from the URL, which "
            "preserves fetched page content, but it does not guarantee the "
            "synthesized summary/diagram format you asked for. If you want, "
            "I can now turn that fetched content into a proper explainer note."
        )
        messages = [
            AIMessage(content="", tool_calls=[
                {"name": "delegate_knowledge", "args": {}, "id": "c1"}
            ]),
            _delegation("delegate_knowledge", caveat_reply),
        ]

        ConversationAgent._auto_complete_plan_steps(USER, messages)

        plan = workspace_service.read_plan(USER)
        assert plan is not None
        # ALL three steps must remain not-done.
        done_count = sum(1 for s in plan["steps"] if s.get("done"))
        assert done_count == 0, (
            f"Caveat guard failed — plan steps were silently auto-completed "
            f"despite the caveat marker: {plan['steps']}"
        )

    def test_auto_completes_clean_delegation(self, ws):
        """When no caveat is present, existing auto-completion still fires."""
        self._make_plan(ws)

        clean_reply = (
            "Saved and readable. Note URL: https://example/notes/x. "
            "The note includes a full synthesized summary and a mermaid diagram."
        )
        messages = [
            AIMessage(content="", tool_calls=[
                {"name": "delegate_knowledge", "args": {}, "id": "c1"}
            ]),
            _delegation("delegate_knowledge", clean_reply),
        ]

        ConversationAgent._auto_complete_plan_steps(USER, messages)

        plan = workspace_service.read_plan(USER)
        done_count = sum(1 for s in plan["steps"] if s.get("done"))
        assert done_count == 3

    def test_skips_when_no_delegation_tools_called(self, ws):
        """Sanity: if no delegation tool was called, nothing is auto-completed."""
        self._make_plan(ws)

        messages = [
            AIMessage(content="thinking out loud"),
        ]
        ConversationAgent._auto_complete_plan_steps(USER, messages)

        plan = workspace_service.read_plan(USER)
        done_count = sum(1 for s in plan["steps"] if s.get("done"))
        assert done_count == 0

    def test_skips_when_delegation_reports_error(self, ws):
        """Sanity: delegation errors don't count as work done."""
        self._make_plan(ws)

        err_reply = "sub-agent failed: timeout"
        messages = [
            AIMessage(content="", tool_calls=[
                {"name": "delegate_knowledge", "args": {}, "id": "c1"}
            ]),
            _delegation("delegate_knowledge", err_reply),
        ]
        ConversationAgent._auto_complete_plan_steps(USER, messages)

        plan = workspace_service.read_plan(USER)
        done_count = sum(1 for s in plan["steps"] if s.get("done"))
        assert done_count == 0

    def test_no_plan_is_a_noop(self, ws):
        """If no plan exists, the guard is a no-op (no crash)."""
        messages = [
            _delegation("delegate_knowledge", "Saved. One caveat: ..."),
        ]
        # Should not raise and should not create a plan.
        ConversationAgent._auto_complete_plan_steps(USER, messages)
        assert workspace_service.read_plan(USER) is None
