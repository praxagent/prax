"""Tests for prax.trace_events — canonical event type vocabulary."""
from __future__ import annotations


class TestTraceEventVocabulary:
    def test_all_expected_types_exist(self):
        from prax.trace_events import TraceEvent
        expected = {"user", "assistant", "system", "tool_call", "tool_result", "audit", "error"}
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
