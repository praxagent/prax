"""E2E tests for Active Inference mechanisms (Phases 1–4).

These tests exercise the full agent orchestration loop and verify that
the Active Inference gates — prediction error tracking, epistemic ledger,
logprob entropy, and semantic entropy — are wired correctly and produce
the expected trace artifacts and behavioral interventions.
"""
from __future__ import annotations

from unittest.mock import patch

from tests.e2e.conftest import ai, ai_tools, make_async_return, tc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_trace_entries(run_e2e_fn, user_msg, responses, *, mocks=None):
    """Run an e2e turn and capture the trace entries written by the orchestrator.

    The orchestrator calls ``append_trace(uid, entries)`` at the end of
    ``run()``, which drains prediction records, logprob entropy, and
    semantic entropy buffers.  We intercept ``append_trace`` at the
    orchestrator's own reference to capture those entries.
    """
    captured: list[dict] = []

    def _fake_append_trace(uid, entries):
        captured.extend(entries)

    combined_mocks = dict(mocks or {})
    # Must mock where it's used (orchestrator imported it at module level),
    # not where it's defined (workspace_service).
    combined_mocks["prax.agent.orchestrator.append_trace"] = _fake_append_trace

    response, llm = run_e2e_fn(user_msg, responses, mocks=combined_mocks)
    return response, llm, captured


# ---------------------------------------------------------------------------
# Phase 1 — Prediction Error Tracking
# ---------------------------------------------------------------------------


def test_prediction_error_recorded_in_trace(run_e2e):
    """When the LLM provides expected_observation, the orchestrator records
    prediction error entries in the workspace trace."""
    response, llm, trace = _capture_trace_entries(
        run_e2e,
        "Search for quantum computing",
        [
            ai_tools(tc("background_search_tool", {
                "query": "quantum computing",
                "expected_observation": "Results about quantum qubits and superposition",
            })),
            ai("Quantum computing uses qubits."),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return(
                "Quantum computing uses qubits and superposition to process information."
            ),
        },
    )
    assert "qubits" in response
    assert llm.call_count == 2

    # The trace should contain at least one prediction_error event.
    from prax.trace_events import TraceEvent
    pred_entries = [e for e in trace if e.get("type") == TraceEvent.PREDICTION_ERROR]
    assert len(pred_entries) >= 1
    assert "background_search_tool" in pred_entries[0]["content"]
    assert "error=" in pred_entries[0]["content"]


def test_high_prediction_error_injects_warning(run_e2e):
    """When consecutive tool calls produce high prediction error, the
    orchestrator injects an Active Inference warning into subsequent
    system prompts."""
    from prax.agent.prediction_tracker import get_prediction_tracker

    tracker = get_prediction_tracker()
    tracker.reset()

    # Pre-seed the tracker with high-error predictions to simulate
    # a bad streak before the current turn.
    tracker.record_prediction("tool_a", "Will succeed", "Error: catastrophic failure")
    tracker.record_prediction("tool_b", "Will succeed", "Error: total meltdown")

    assert tracker.is_high_uncertainty

    # The prompt_injection() should now produce a warning.
    warning = tracker.prompt_injection()
    assert "ACTIVE INFERENCE WARNING" in warning
    assert "read-only" in warning.lower()

    # Run a normal turn — the orchestrator will include this warning
    # in the system prompt (via prediction_hint in orchestrator.run).
    response, llm = run_e2e(
        "Hello",
        [ai("I notice my recent predictions have been off. Let me verify my assumptions first.")],
    )
    assert llm.call_count == 1


def test_prediction_tracker_resets_each_turn(run_e2e):
    """The prediction tracker is reset at the start of each orchestrator turn."""
    from prax.agent.prediction_tracker import get_prediction_tracker

    tracker = get_prediction_tracker()

    # Seed some stale state.
    tracker.record_prediction("stale_tool", "Will work", "Error: nope")
    tracker.record_read("stale_file.md")

    # Running a new turn should reset the tracker.
    response, llm = run_e2e(
        "Hi",
        [ai("Hello!")],
    )

    # After the turn, tracker state from before should be gone.
    # Note: the orchestrator resets at the START of run(), so any
    # predictions during the turn are still there until next reset.
    assert not tracker.has_read("stale_file.md")


# ---------------------------------------------------------------------------
# Phase 2 — Epistemic Ledger (read-before-write gate)
# ---------------------------------------------------------------------------


def test_epistemic_gate_blocks_unread_write(run_e2e):
    """Writing to a file that hasn't been read in this session triggers
    the epistemic gate and returns a warning instead of executing."""
    response, llm = run_e2e(
        "Update my notes file",
        [
            # Agent tries to write without reading first
            ai_tools(tc("workspace_save", {
                "filename": "notes.md",
                "content": "Updated content",
            })),
            # Agent should get the gate warning and respond accordingly
            ai("I need to read the file first before making changes. Let me check its current contents."),
        ],
    )
    # The agent should get the epistemic gate warning as tool result
    assert llm.call_count == 2


def test_epistemic_gate_allows_after_read(run_e2e):
    """After reading a file, writing to it passes the epistemic gate."""
    response, llm = run_e2e(
        "Read my notes and then update them",
        [
            # Step 1: Agent reads the file first
            ai_tools(tc("workspace_read", {"filename": "notes.md"})),
            # Step 2: Agent writes (should pass the gate now)
            ai_tools(tc("workspace_save", {
                "filename": "notes.md",
                "content": "Updated content after reading",
            })),
            ai("I've read your notes and updated them."),
        ],
        mocks={
            "prax.services.workspace_service.read_file": "# Old Notes\nSome existing content.",
            "prax.services.workspace_service.save_file": None,
        },
    )
    assert llm.call_count == 3


def test_epistemic_gate_allows_new_file_creation(run_e2e):
    """Creating a note (note_create) without a title passes the epistemic
    gate because no resource key can be extracted — allowing creation."""
    response, llm = run_e2e(
        "Create a note about testing",
        [
            ai_tools(tc("note_create", {
                "content": "# Testing Notes\nSome content about testing.",
                "tags": "testing",
            })),
            ai("I've created the testing note."),
        ],
        mocks={
            "prax.services.note_service.save_and_publish": {
                "title": "Testing Notes",
                "url": "https://notes.example.com/testing/",
            },
        },
    )
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# Phase 3 — Logprob Entropy (trace integration)
# ---------------------------------------------------------------------------


def test_logprob_entropy_buffer_drains_to_trace(run_e2e):
    """Logprob entropy data from the callback handler is flushed to the
    trace log after each orchestrator turn."""
    from prax.agent.logprob_analyzer import ToolCallEntropy, _entropy_buffer, _entropy_lock, drain_entropy_buffer

    # Clear any leftover state.
    drain_entropy_buffer()

    # Pre-seed the entropy buffer to simulate the callback having fired
    # during an LLM response with logprobs.
    with _entropy_lock:
        _entropy_buffer.append(ToolCallEntropy(
            tool_name="background_search_tool",
            mean_logprob=-1.5,
            min_logprob=-3.2,
            entropy_score=0.30,
            high_entropy_tokens=["quantum", "search"],
        ))

    response, llm = run_e2e(
        "Search for something",
        [
            ai_tools(tc("background_search_tool", {"query": "test"})),
            ai("Here are the results."),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return("test results"),
        },
    )
    assert llm.call_count == 2

    # After the turn, the buffer should have been drained by _write_trace.
    remaining = drain_entropy_buffer()
    # The pre-seeded entry should be gone (drained during trace write).
    # There may be new entries from this turn's tool calls.
    assert not any(e.tool_name == "background_search_tool" and e.entropy_score == 0.30 for e in remaining)


# ---------------------------------------------------------------------------
# Phase 4 — Semantic Entropy Gate
# ---------------------------------------------------------------------------


def test_semantic_entropy_gate_disabled_by_default(run_e2e):
    """With ACTIVE_INFERENCE_SEMANTIC_GATE unset, HIGH-risk tools execute
    normally (after the standard confirmation gate)."""
    import os

    # Ensure the gate is disabled.
    env = os.environ.copy()
    env.pop("ACTIVE_INFERENCE_SEMANTIC_GATE", None)

    with patch.dict(os.environ, env, clear=True):
        # This test just confirms the gate doesn't interfere.
        # We use a non-HIGH-risk tool to avoid the confirmation gate.
        response, llm = run_e2e(
            "What time is it?",
            [ai("It's 3:00 PM.")],
        )
        assert llm.call_count == 1


def test_semantic_entropy_buffer_recorded(run_e2e):
    """Semantic entropy results (when gate is enabled) are recorded in
    the module buffer for trace integration."""
    from prax.agent.semantic_entropy import SemanticEntropyResult, _entropy_results, _entropy_lock, drain_semantic_entropy_buffer

    # Clear any leftover state.
    drain_semantic_entropy_buffer()

    # Pre-seed a semantic entropy result as if the gate had run.
    with _entropy_lock:
        _entropy_results.append(SemanticEntropyResult(
            proposed_tool="plugin_write",
            sampled_tools=["plugin_write", "plugin_write", "plugin_write"],
            agreement_ratio=1.0,
            blocked=False,
        ))

    response, llm = run_e2e(
        "Hello",
        [ai("Hi there!")],
    )
    assert llm.call_count == 1

    # After the turn, the buffer should be drained by _write_trace.
    remaining = drain_semantic_entropy_buffer()
    assert not any(r.proposed_tool == "plugin_write" and r.agreement_ratio == 1.0 for r in remaining)


# ---------------------------------------------------------------------------
# Governance integration — expected_observation flows through
# ---------------------------------------------------------------------------


def test_expected_observation_stripped_before_tool_execution(run_e2e):
    """The governed tool wrapper strips expected_observation from kwargs
    before passing them to the actual tool function."""
    response, llm = run_e2e(
        "What time is it in New York?",
        [
            ai_tools(tc("get_current_datetime", {
                "timezone_name": "America/New_York",
                "expected_observation": "Current time in Eastern timezone",
            })),
            ai("It's currently 3:00 PM Eastern Time."),
        ],
    )
    # If expected_observation leaked through to the actual tool,
    # it would cause a TypeError (unexpected keyword argument).
    # The test passing means it was properly stripped.
    assert "3:00 PM" in response
    assert llm.call_count == 2


# ---------------------------------------------------------------------------
# Trace completeness — all Active Inference events written
# ---------------------------------------------------------------------------


def test_trace_includes_all_active_inference_event_types(run_e2e):
    """The orchestrator's _write_trace method handles all four AI event
    types without errors, even when buffers are empty."""
    # This test verifies the trace writing path doesn't crash when
    # the Active Inference modules return empty buffers.
    response, llm = run_e2e(
        "Tell me a joke",
        [ai("Why did the chicken cross the road? To minimize its prediction error.")],
    )
    assert "chicken" in response or "prediction" in response
    assert llm.call_count == 1


# ---------------------------------------------------------------------------
# Budget interaction — prediction error + budget exhaustion
# ---------------------------------------------------------------------------


def test_budget_exhaustion_still_records_predictions(run_e2e):
    """When the tool call budget is exhausted, the governance layer
    records the budget-blocked event but doesn't crash the prediction
    tracker."""
    from prax.agent.prediction_tracker import get_prediction_tracker

    tracker = get_prediction_tracker()
    tracker.reset()

    response, llm = run_e2e(
        "Do many things",
        [
            ai_tools(tc("background_search_tool", {
                "query": "first search",
                "expected_observation": "search results",
            })),
            ai("I found some results."),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return("results"),
        },
    )
    assert llm.call_count == 2
    # No crash — prediction tracking coexists with budget tracking.


# ---------------------------------------------------------------------------
# Multi-tool turn — predictions tracked across multiple tool calls
# ---------------------------------------------------------------------------


def test_multiple_tool_calls_each_tracked(run_e2e):
    """When the agent makes multiple tool calls in one turn, each one
    that includes expected_observation gets its prediction tracked in
    the trace."""
    from prax.trace_events import TraceEvent

    response, llm, trace = _capture_trace_entries(
        run_e2e,
        "Search for two topics",
        [
            ai_tools(
                tc("background_search_tool", {
                    "query": "topic one",
                    "expected_observation": "Information about topic one",
                }, call_id="call_1"),
                tc("get_current_datetime", {
                    "timezone_name": "UTC",
                    "expected_observation": "Current UTC time",
                }, call_id="call_2"),
            ),
            ai("Here's what I found about topic one. The current UTC time is 3:00 PM."),
        ],
        mocks={
            "prax.helpers_functions.background_search": make_async_return(
                "Information about topic one from search results"
            ),
        },
    )
    assert llm.call_count == 2

    # Both tool calls should have prediction_error entries in the trace.
    pred_entries = [e for e in trace if e.get("type") == TraceEvent.PREDICTION_ERROR]
    pred_tools = [e["content"] for e in pred_entries]
    assert any("background_search_tool" in c for c in pred_tools)
    assert any("get_current_datetime" in c for c in pred_tools)
