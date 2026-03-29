"""Integration workflow tests — real Prax runs evaluated by an LLM judge.

Each test sends a real message through the Prax pipeline (real LLM, real tools),
captures the response + execution trace + workspace artifacts, then has an LLM
judge evaluate whether the result meets expectations.

Artifacts are saved to tests/integration/.artifacts/ for human review.

Run::

    # All integration tests
    pytest tests/integration/ -m integration -v

    # Single scenario
    pytest tests/integration/test_workflows.py -k create_simple_note -v

    # With visible output
    pytest tests/integration/ -m integration -v -s
"""
from __future__ import annotations

from fnmatch import fnmatch

import pytest

from tests.integration.scenarios import SCENARIOS

pytestmark = pytest.mark.integration


def _count_tool_calls(result) -> int:
    """Count total tool calls across all spans."""
    return sum(s.get("tool_calls", 0) for s in result.span_nodes)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_workflow(run_prax, judge, save_artifacts, scenario):
    """Run a scenario through real Prax and evaluate with the LLM judge."""

    # ---- Act: run through real Prax ----
    result = run_prax(
        scenario.message,
        timeout=scenario.max_duration,
        require_plugins=scenario.require_plugins,
    )

    # ---- Hard checks (no LLM needed) ----

    # Must produce a non-empty response
    assert result.response, "Agent produced no response"
    assert result.response != "[ScriptedLLM: script exhausted]", "Used scripted LLM — real LLM not configured"

    # Must complete within timeout
    assert result.duration_seconds < scenario.max_duration, (
        f"Took {result.duration_seconds:.1f}s, limit is {scenario.max_duration}s"
    )

    # Expected artifacts must exist (glob matching)
    for pattern in scenario.expected_artifacts:
        matches = [f for f in result.workspace_files if fnmatch(f, pattern)]
        assert matches, (
            f"Expected workspace file matching '{pattern}', "
            f"but found: {list(result.workspace_files.keys())}"
        )

    # If no artifacts expected, verify none were created (except trace.log)
    if not scenario.expected_artifacts:
        non_trace_files = [f for f in result.workspace_files if f != "trace.log"]
        # This is a soft check — don't fail, just note it
        if non_trace_files:
            print(f"  NOTE: Unexpected workspace files: {non_trace_files}")

    # Tool call count guardrails — catch runaway loops and missing tool usage
    total_tool_calls = _count_tool_calls(result)
    llm_calls = result.cost.get("total_llm_calls", 0)

    if scenario.min_tool_calls and total_tool_calls < scenario.min_tool_calls:
        # Use LLM calls as a fallback — some tools are called within the
        # LangGraph recursion and don't get separate spans.
        if llm_calls < scenario.min_tool_calls:
            print(
                f"  WARNING: Only {total_tool_calls} tool calls "
                f"(expected >= {scenario.min_tool_calls})"
            )

    if scenario.max_tool_calls:
        assert llm_calls <= scenario.max_tool_calls, (
            f"Runaway detected: {llm_calls} LLM calls exceeds limit of "
            f"{scenario.max_tool_calls}. This suggests the agent entered a loop. "
            f"Cost so far: ${result.cost.get('total_cost_usd', 0):.4f}"
        )

    # ---- LLM judge evaluation ----
    verdict = judge(
        result,
        message=scenario.message,
        expected_flow=scenario.expected_flow,
        quality_criteria=scenario.quality_criteria,
    )

    # ---- Save artifacts for human review (always, pass or fail) ----
    artifact_dir = save_artifacts(scenario.name, result, verdict)
    cost = result.cost
    print(f"\n  Artifacts saved to: {artifact_dir}")
    print(f"  Duration: {result.duration_seconds:.1f}s")
    print(f"  Cost: ${cost.get('total_cost_usd', 0):.4f} "
          f"({cost.get('total_input_tokens', 0)} in / "
          f"{cost.get('total_output_tokens', 0)} out, "
          f"{cost.get('total_llm_calls', 0)} LLM calls)")
    print(f"  Spans: {len(result.span_nodes)}")
    print(f"  Tool calls: {total_tool_calls} "
          f"(allowed range: {scenario.min_tool_calls}-{scenario.max_tool_calls})")
    print(f"  Files: {list(result.workspace_files.keys())}")
    if result.tier_choices:
        tier_summary = {}
        for tc in result.tier_choices:
            key = f"{tc.get('tier_requested', '?')}→{tc.get('model', '?')}"
            tier_summary[key] = tier_summary.get(key, 0) + 1
        # Per-span breakdown for A/B visibility
        span_tiers: dict[str, list[str]] = {}
        for tc in result.tier_choices:
            sn = tc.get("span_name", "?")
            span_tiers.setdefault(sn, []).append(tc.get("tier_requested", "?"))
        print(f"  Tiers: {tier_summary}")
        print(f"  Tier-by-span: {dict(span_tiers)}")
    print(f"  Judge: {'PASS' if verdict.passed else 'FAIL'} — {verdict.reasoning}")

    # ---- Assert judge verdict ----
    assert verdict.passed, (
        f"LLM judge failed scenario '{scenario.name}':\n"
        f"  Reasoning: {verdict.reasoning}\n"
        f"  Flow correct: {verdict.flow_correct}\n"
        f"  Output correct: {verdict.output_correct}\n"
        f"  Issues: {verdict.issues}\n"
        f"  Artifacts: {artifact_dir}"
    )
