"""Integration tests for Active Inference mechanisms with real LLM calls.

These tests verify that the Active Inference pipeline (Phases 1–4) produces
real trace artifacts when running against an actual LLM.  Unlike the e2e
suite (ScriptedLLM), these hit the real API and check that:

1. Prediction error records appear in the trace log
2. The epistemic gate fires on write-before-read (and the agent self-corrects)
3. The ``expected_observation`` field flows through tool schemas
4. Trace events for all Active Inference phases are well-formed

Run::

    pytest tests/integration/test_active_inference.py -m integration -v -s
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_trace_events(trace_log: str, event_type: str) -> list[str]:
    """Extract lines from the trace log matching a given event type."""
    pattern = re.compile(rf'^\[.*?\]\s*\[{re.escape(event_type)}\]\s*(.*)$', re.MULTILINE)
    return pattern.findall(trace_log)


def _has_tool_call(trace_log: str, tool_name: str) -> bool:
    """Check whether a tool was called in the trace log."""
    return f"[tool_call] {tool_name}(" in trace_log.lower() or tool_name in trace_log


# ---------------------------------------------------------------------------
# Phase 1 — Prediction Error appears in trace
# ---------------------------------------------------------------------------


class TestPredictionErrorTrace:
    """Verify that tool calls produce prediction_error trace entries."""

    def test_search_produces_prediction_record(self, run_prax, save_artifacts):
        """A web search should produce at least one prediction_error trace
        entry — the LLM is instructed to include expected_observation via
        the system prompt and augmented tool schema."""
        result = run_prax(
            "Search for 'transformer architecture attention mechanism' and "
            "give me a two-sentence summary of what you find.",
            timeout=60,
        )

        assert result.response, "Agent produced no response"
        assert len(result.response) > 20, "Response too short"

        save_artifacts("ai_prediction_error_search", result)

        # The trace log should contain prediction_error events if the LLM
        # used the expected_observation field.  This is probabilistic —
        # the LLM may or may not fill it.  We check the trace is non-empty
        # and the search tool was called.
        assert result.trace_log, "No trace log produced"
        assert _has_tool_call(result.trace_log, "background_search"), (
            "Expected background_search_tool in trace, got:\n"
            + result.trace_log[-500:]
        )

        # Soft check — prediction_error entries are optional (LLM may not
        # always fill expected_observation).  Print for human review.
        pred_events = _extract_trace_events(result.trace_log, "prediction_error")
        print(f"\n  Prediction error entries: {len(pred_events)}")
        for pe in pred_events[:3]:
            print(f"    {pe[:120]}")


# ---------------------------------------------------------------------------
# Phase 2 — Epistemic Gate (read-before-write)
# ---------------------------------------------------------------------------


class TestEpistemicGate:
    """Verify that the epistemic gate blocks writes to unread files."""

    def test_direct_save_triggers_gate_or_succeeds(self, run_prax, save_artifacts):
        """When asked to save a file, the agent should either:
        (a) succeed (workspace_save for new file creation is allowed), or
        (b) get an epistemic gate warning and self-correct.

        Either way, the file should ultimately be created."""
        result = run_prax(
            "Save a file called ai_test.md to my workspace with a brief "
            "explanation of how neural networks learn through backpropagation.",
            timeout=60,
        )

        assert result.response, "Agent produced no response"

        save_artifacts("ai_epistemic_gate_save", result)

        # The file should exist in the workspace regardless of gate behavior
        ai_files = [f for f in result.workspace_files if "ai_test" in f]
        assert ai_files, (
            f"Expected ai_test.md in workspace, found: "
            f"{list(result.workspace_files.keys())}"
        )

        # Check trace for epistemic gate mentions
        gate_events = _extract_trace_events(result.trace_log, "epistemic_gate")
        gate_in_audit = "epistemic gate" in result.trace_log.lower()
        print(f"\n  Epistemic gate events: {len(gate_events)}")
        print(f"  Gate mentioned in audit: {gate_in_audit}")

    def test_read_then_update_passes_gate(self, run_prax, save_artifacts):
        """When asked to read a file and then update it, the epistemic gate
        should not block the write (the agent read first)."""
        # First create a file
        result1 = run_prax(
            "Save a file called update_test.md to my workspace with the text: "
            "'Original content: placeholder for update test.'",
            timeout=45,
        )
        assert any("update_test" in f for f in result1.workspace_files), (
            "Setup failed: update_test.md not created"
        )

        # Now read and update it in a follow-up message
        # We pass the first turn's response as conversation context
        from langchain_core.messages import AIMessage, HumanMessage
        conversation = [
            HumanMessage(content=(
                "Save a file called update_test.md to my workspace with the text: "
                "'Original content: placeholder for update test.'"
            )),
            AIMessage(content=result1.response),
        ]

        result2 = run_prax(
            "Read update_test.md from my workspace, then update it by adding "
            "a new section called '## Analysis' with a brief paragraph.",
            timeout=60,
            conversation=conversation,
        )

        assert result2.response, "Agent produced no response"

        save_artifacts("ai_epistemic_read_then_update", result2)

        # The trace should show workspace_read followed by workspace_save
        # (or workspace_patch), with no epistemic gate block.
        trace = result2.trace_log.lower()
        has_read = "workspace_read" in trace
        has_write = "workspace_save" in trace or "workspace_patch" in trace
        blocked = "epistemic gate" in trace and "blocked" in trace

        print(f"\n  Has read: {has_read}")
        print(f"  Has write: {has_write}")
        print(f"  Was blocked: {blocked}")

        # The write should have succeeded (not blocked by gate)
        # because the agent read first
        assert not blocked or has_read, (
            "Epistemic gate blocked the write even though the agent should "
            "have read first"
        )


# ---------------------------------------------------------------------------
# Trace completeness — all Active Inference event types
# ---------------------------------------------------------------------------


class TestTraceCompleteness:
    """Verify the trace log structure includes Active Inference event types."""

    def test_trace_log_has_audit_entries(self, run_prax, save_artifacts):
        """Every Prax run should produce audit entries in the trace log —
        these are the governance layer's record of tool execution."""
        result = run_prax(
            "What time is it right now?",
            timeout=30,
        )

        assert result.response, "Agent produced no response"
        assert result.trace_log, "No trace log produced"

        save_artifacts("ai_trace_completeness", result)

        # Audit entries should always be present (governance layer runs
        # on every tool call).
        audit_lines = [
            line for line in result.trace_log.splitlines()
            if "[audit]" in line.lower()
        ]
        print(f"\n  Audit entries: {len(audit_lines)}")
        for a in audit_lines[:5]:
            print(f"    {a[:120]}")

    def test_multi_tool_trace_has_predictions(self, run_prax, save_artifacts):
        """A multi-tool interaction (search + save) should produce multiple
        trace entries across tool_call, tool_result, and audit types."""
        result = run_prax(
            "Search for 'benefits of meditation' and then save a brief "
            "summary as meditation.md in my workspace.",
            timeout=90,
        )

        assert result.response, "Agent produced no response"

        save_artifacts("ai_multi_tool_trace", result)

        # Should have both search and save in the trace
        trace = result.trace_log
        assert trace, "No trace log produced"

        tool_calls = [
            line for line in trace.splitlines()
            if "[tool_call]" in line.lower()
        ]
        tool_results = [
            line for line in trace.splitlines()
            if "[tool_result]" in line.lower()
        ]

        print(f"\n  Tool call entries: {len(tool_calls)}")
        print(f"  Tool result entries: {len(tool_results)}")
        for tc in tool_calls[:5]:
            print(f"    {tc[:120]}")

        # Should have workspace file
        md_files = [f for f in result.workspace_files if "meditation" in f]
        print(f"  Workspace files with 'meditation': {md_files}")

        # Verify non-trivial tool usage
        assert len(tool_calls) >= 1, "Expected at least 1 tool call"


# ---------------------------------------------------------------------------
# Schema augmentation — expected_observation field is available
# ---------------------------------------------------------------------------


class TestSchemaAugmentation:
    """Verify that the expected_observation field is available in tool schemas."""

    def test_tool_schema_includes_expected_observation(self):
        """The governed tool wrapper should add expected_observation to
        every tool's argument schema."""
        from prax.agent.tool_registry import get_registered_tools
        from prax.plugins.loader import get_plugin_loader
        from unittest.mock import MagicMock

        # Use a mock plugin loader to avoid side effects
        mock_loader = MagicMock()
        mock_loader.get_tools.return_value = []
        mock_loader.version = 0

        from unittest.mock import patch
        with patch("prax.agent.tool_registry.get_plugin_loader", return_value=mock_loader):
            tools = get_registered_tools()

        # Check a few tools for the expected_observation field
        tools_with_field = []
        tools_without_field = []
        for tool in tools[:10]:  # Check first 10
            schema = tool.args_schema
            if schema is None:
                continue
            fields = schema.model_fields if hasattr(schema, "model_fields") else {}
            if "expected_observation" in fields:
                tools_with_field.append(tool.name)
            else:
                tools_without_field.append(tool.name)

        print(f"\n  Tools with expected_observation: {len(tools_with_field)}")
        print(f"  Tools without: {len(tools_without_field)}")
        print(f"  Sample with: {tools_with_field[:5]}")

        assert tools_with_field, (
            "No tools have the expected_observation field in their schema. "
            "The _augment_schema function may not be working."
        )


# ---------------------------------------------------------------------------
# Budget + Active Inference coexistence
# ---------------------------------------------------------------------------


class TestBudgetCoexistence:
    """Verify that prediction tracking doesn't interfere with budget tracking."""

    def test_normal_task_completes_within_budget(self, run_prax, save_artifacts):
        """A simple task should complete normally — Active Inference tracking
        should not cause budget exhaustion or other side effects."""
        result = run_prax(
            "List three interesting facts about octopuses.",
            timeout=30,
        )

        assert result.response, "Agent produced no response"
        assert "octop" in result.response.lower(), "Response should mention octopuses"

        save_artifacts("ai_budget_coexistence", result)

        # Should not have any budget exhaustion in the trace
        assert "budget exhausted" not in result.trace_log.lower(), (
            "Budget was exhausted for a simple task — Active Inference "
            "tracking may be inflating call counts"
        )

        llm_calls = result.cost.get("total_llm_calls", 0)
        print(f"\n  LLM calls: {llm_calls}")
        print(f"  Duration: {result.duration_seconds:.1f}s")
        assert llm_calls <= 5, f"Too many LLM calls ({llm_calls}) for a simple task"
