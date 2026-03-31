"""Tests for prax.trace_events — canonical event type vocabulary."""
from __future__ import annotations


class TestTraceEventVocabulary:
    def test_all_expected_types_exist(self):
        from prax.trace_events import TraceEvent
        expected = {
            "user", "assistant", "system", "tool_call", "tool_result", "audit", "error",
            "plugin_import", "plugin_activate", "plugin_block",
            "plugin_rollback", "plugin_remove", "plugin_security_warn",
            "tier_choice",
            "think",
            "prediction_error", "epistemic_gate", "logprob_entropy", "semantic_entropy",
        }
        assert TraceEvent.values() == expected

    def test_is_valid_known_types(self):
        from prax.trace_events import TraceEvent
        assert TraceEvent.is_valid("user")
        assert TraceEvent.is_valid("audit")
        assert TraceEvent.is_valid("tool_call")

    def test_is_valid_rejects_unknown(self):
        from prax.trace_events import TraceEvent
        assert not TraceEvent.is_valid("unknown")
        assert not TraceEvent.is_valid("foo")
        assert not TraceEvent.is_valid("")

    def test_enum_values_are_strings(self):
        from prax.trace_events import TraceEvent
        # TraceEvent inherits from str, so values work as dict keys/values.
        assert TraceEvent.AUDIT == "audit"
        assert TraceEvent.TOOL_CALL == "tool_call"
        assert isinstance(TraceEvent.USER, str)

    def test_can_be_used_as_dict_type(self):
        """TraceEvent values work seamlessly as entry['type'] values."""
        from prax.trace_events import TraceEvent
        entry = {"type": TraceEvent.AUDIT, "content": "test"}
        assert entry["type"] == "audit"
        assert entry["type"].upper() == "AUDIT"


class TestTierChoiceInTraceGraph:
    """Verify tier choices are tracked in the execution graph."""

    def test_span_node_stores_tier_choices(self):
        from prax.agent.trace import SpanNode
        node = SpanNode(
            span_id="s1", name="test", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
        )
        assert node.tier_choices == []
        node.tier_choices.append({"tier_requested": "low", "model": "gpt-nano"})
        assert len(node.tier_choices) == 1

    def test_complete_node_accepts_tier_choices(self):
        from prax.agent.trace import ExecutionGraph, SpanNode
        graph = ExecutionGraph("t1")
        node = SpanNode(
            span_id="s1", name="test", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
        )
        graph.add_node(node)
        graph.complete_node(
            "s1", status="completed",
            tier_choices=[{"tier_requested": "high", "model": "gpt-5.4"}],
        )
        assert len(graph._nodes["s1"].tier_choices) == 1
        assert graph._nodes["s1"].tier_choices[0]["tier_requested"] == "high"

    def test_graph_summary_includes_tier_info(self):
        from prax.agent.trace import ExecutionGraph, SpanNode
        graph = ExecutionGraph("t1")
        node = SpanNode(
            span_id="s1", name="orchestrator", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
            tier_choices=[
                {"tier_requested": "low", "model": "gpt-nano"},
                {"tier_requested": "low", "model": "gpt-nano"},
                {"tier_requested": "medium", "model": "gpt-mini"},
            ],
        )
        graph.add_node(node)
        summary = graph.get_summary()
        assert "tiers:" in summary
        assert "low→gpt-nano x2" in summary
        assert "medium→gpt-mini" in summary

    def test_get_all_tier_choices(self):
        from prax.agent.trace import ExecutionGraph, SpanNode, get_all_tier_choices
        graph = ExecutionGraph("t1")
        graph.add_node(SpanNode(
            span_id="s1", name="orch", parent_id=None,
            trace_id="t1", spoke_or_category="orchestrator",
            tier_choices=[{"ts": 1, "tier_requested": "low", "model": "nano"}],
        ))
        graph.add_node(SpanNode(
            span_id="s2", name="research", parent_id="s1",
            trace_id="t1", spoke_or_category="research",
            tier_choices=[{"ts": 2, "tier_requested": "medium", "model": "mini"}],
        ))
        choices = get_all_tier_choices(graph)
        assert len(choices) == 2
        assert choices[0]["model"] == "nano"
        assert choices[1]["model"] == "mini"

    def test_tier_choice_event_type_is_valid(self):
        from prax.trace_events import TraceEvent
        assert TraceEvent.is_valid("tier_choice")
        assert TraceEvent.TIER_CHOICE == "tier_choice"


class TestOrchestratorUsesTraceEvents:
    """Verify the orchestrator emits entries using TraceEvent constants."""

    def test_orchestrator_imports_trace_events(self):
        import ast
        from pathlib import Path
        source = (Path(__file__).parent.parent / "prax" / "agent" / "orchestrator.py").read_text()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "prax.trace_events":
                imports.extend(alias.name for alias in node.names)
        assert "TraceEvent" in imports

    def test_orchestrator_no_raw_type_strings(self):
        """The orchestrator should use TraceEvent.X, not raw 'type': 'user' strings."""
        import ast
        from pathlib import Path
        source = (Path(__file__).parent.parent / "prax" / "agent" / "orchestrator.py").read_text()
        tree = ast.parse(source)

        # Look for dict entries like {"type": "user"} (string literal as type value).
        # These should be TraceEvent.X instead.
        raw_type_strings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=False):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "type"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        raw_type_strings.append(value.value)
        assert raw_type_strings == [], (
            f"orchestrator.py uses raw type strings instead of TraceEvent: {raw_type_strings}"
        )
