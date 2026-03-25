"""Tests for prax.agent.governed_tool — the single governance choke point."""
from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(name: str, func=None):
    """Create a minimal LangChain tool for testing."""
    if func is None:
        def func(x: str = "") -> str:
            return f"executed:{x}"
    return StructuredTool.from_function(func=func, name=name, description=f"test {name}")


def _reset():
    """Clear governed_tool module state between tests."""
    from prax.agent.governed_tool import _audit_buffer, _high_risk_seen
    _audit_buffer.clear()
    _high_risk_seen.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGovernedToolWrapping:
    def test_wrapping_preserves_name_and_description(self):
        from prax.agent.governed_tool import wrap_with_governance
        inner = _make_tool("note_list")
        governed = wrap_with_governance(inner)
        assert governed.name == "note_list"
        assert governed.description == inner.description

    def test_wrapping_returns_structured_tool(self):
        from prax.agent.governed_tool import wrap_with_governance
        inner = _make_tool("todo_list")
        governed = wrap_with_governance(inner)
        assert isinstance(governed, StructuredTool)


class TestLowRiskToolExecution:
    def test_low_risk_executes_immediately(self):
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("get_current_datetime")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "now"})
        assert "executed:now" in result

    def test_low_risk_creates_audit_entry(self):
        from prax.agent.governed_tool import _audit_buffer, wrap_with_governance
        _reset()
        inner = _make_tool("get_current_datetime")
        governed = wrap_with_governance(inner)
        governed.invoke({"x": "now"})
        assert len(_audit_buffer) >= 1
        assert _audit_buffer[-1]["tool_name"] == "get_current_datetime"


class TestMediumRiskToolExecution:
    def test_medium_risk_executes_immediately(self):
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("note_create")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "test"})
        assert "executed:test" in result

    def test_medium_risk_logged(self):
        from prax.agent.governed_tool import _audit_buffer, wrap_with_governance
        _reset()
        inner = _make_tool("note_create")
        governed = wrap_with_governance(inner)
        governed.invoke({"x": "test"})
        assert _audit_buffer[-1]["risk"] == "medium"


class TestHighRiskToolBlocking:
    def test_high_risk_blocked_on_first_call(self):
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("sandbox_execute")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "rm -rf"})
        assert "HIGH risk" in result
        assert "confirm" in result.lower()

    def test_high_risk_audit_shows_blocked(self):
        from prax.agent.governed_tool import _audit_buffer, wrap_with_governance
        _reset()
        inner = _make_tool("sandbox_execute")
        governed = wrap_with_governance(inner)
        governed.invoke({"x": "rm -rf"})
        assert "BLOCKED" in _audit_buffer[-1]["result"]

    def test_high_risk_executes_on_second_call(self):
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("sandbox_execute")
        governed = wrap_with_governance(inner)
        # First call: blocked.
        result1 = governed.invoke({"x": "echo hello"})
        assert "HIGH risk" in result1
        # Second call: executes.
        result2 = governed.invoke({"x": "echo hello"})
        assert "executed:echo hello" in result2

    def test_high_risk_second_call_audit_not_blocked(self):
        from prax.agent.governed_tool import _audit_buffer, wrap_with_governance
        _reset()
        inner = _make_tool("sandbox_execute")
        governed = wrap_with_governance(inner)
        governed.invoke({"x": "test"})  # blocked
        governed.invoke({"x": "test"})  # executed
        assert len(_audit_buffer) == 2
        assert "BLOCKED" in _audit_buffer[0]["result"]
        assert "BLOCKED" not in (_audit_buffer[1].get("result") or "")

    def test_different_high_risk_tools_each_blocked_once(self):
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        tool_a = wrap_with_governance(_make_tool("sandbox_execute"))
        tool_b = wrap_with_governance(_make_tool("plugin_write"))
        # Each tool blocked on first call.
        assert "HIGH risk" in tool_a.invoke({"x": "a"})
        assert "HIGH risk" in tool_b.invoke({"x": "b"})
        # Each executes on second call.
        assert "executed:a" in tool_a.invoke({"x": "a"})
        assert "executed:b" in tool_b.invoke({"x": "b"})


class TestAuditDrain:
    def test_drain_returns_and_clears(self):
        from prax.agent.governed_tool import _audit_buffer, drain_audit_log, wrap_with_governance
        _reset()
        inner = _make_tool("note_list")
        governed = wrap_with_governance(inner)
        governed.invoke({"x": "a"})
        governed.invoke({"x": "b"})
        assert len(_audit_buffer) == 2

        drained = drain_audit_log()
        assert len(drained) == 2
        assert len(_audit_buffer) == 0

    def test_drain_resets_high_risk_seen(self):
        from prax.agent.governed_tool import _high_risk_seen, drain_audit_log, wrap_with_governance
        _reset()
        inner = _make_tool("sandbox_execute")
        governed = wrap_with_governance(inner)
        governed.invoke({"x": "a"})  # blocked, adds to _high_risk_seen
        assert "sandbox_execute" in _high_risk_seen
        drain_audit_log()
        assert len(_high_risk_seen) == 0
        # After drain, first call should block again (new turn).
        result = governed.invoke({"x": "a"})
        assert "HIGH risk" in result

    def test_drain_idempotent(self):
        from prax.agent.governed_tool import drain_audit_log
        _reset()
        assert drain_audit_log() == []


class TestErrorAudit:
    def test_exception_still_audited(self):
        from prax.agent.governed_tool import _audit_buffer, wrap_with_governance

        def _fail(x: str = "") -> str:
            raise ValueError("boom")

        _reset()
        inner = _make_tool("note_list", func=_fail)
        governed = wrap_with_governance(inner)
        with pytest.raises(ValueError, match="boom"):
            governed.invoke({"x": "test"})
        assert len(_audit_buffer) == 1
        assert "ERROR" in _audit_buffer[0]["result"]


class TestToolMetadataPrecedence:
    def test_tool_metadata_takes_precedence(self):
        """A tool with _risk_level=HIGH should be gated even if the central
        map classifies it as MEDIUM (or vice-versa)."""
        from prax.agent.action_policy import RiskLevel, get_risk_level
        from prax.agent.governed_tool import wrap_with_governance

        _reset()

        # Pick a tool name that is MEDIUM in the central map.
        tool_name = "note_create"
        assert get_risk_level(tool_name) is RiskLevel.MEDIUM

        # Create a tool with that name but override to HIGH via metadata.
        inner = _make_tool(tool_name)
        inner._risk_level = RiskLevel.HIGH

        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "test"})
        # Should be blocked because _risk_level (HIGH) takes precedence.
        assert "HIGH risk" in result
        assert "confirm" in result.lower()


class TestEpistemicTagging:
    """Verify that tool results are tagged with source-reliability metadata."""

    def test_informational_tool_tagged(self):
        """background_search_tool results should be tagged INFORMATIONAL."""
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("background_search_tool")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "test query"})
        assert "[INFORMATIONAL SOURCE" in result
        assert "Do NOT state specific numbers" in result
        # Original result is still present after the tag.
        assert "executed:test query" in result

    def test_verified_tool_tagged(self):
        """flight_search results should be tagged VERIFIED."""
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("flight_search")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "JFK CDG"})
        assert "[VERIFIED SOURCE" in result
        assert "executed:JFK CDG" in result

    def test_indicative_tool_tagged(self):
        """browser_read_page results should be tagged INDICATIVE."""
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("browser_read_page")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "page"})
        assert "[INDICATIVE SOURCE" in result
        assert "approximate" in result.lower()

    def test_uncatalogued_tool_not_tagged(self):
        """Tools not in TOOL_CAPABILITIES should pass results through unchanged."""
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("get_current_datetime")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "now"})
        assert "[INFORMATIONAL" not in result
        assert "[VERIFIED" not in result
        assert "[INDICATIVE" not in result
        assert result == "executed:now"

    def test_epistemic_note_included(self):
        """The epistemic_note from TOOL_CAPABILITIES should appear in tagged results."""
        from prax.agent.governed_tool import wrap_with_governance
        _reset()
        inner = _make_tool("fetch_url_content")
        governed = wrap_with_governance(inner)
        result = governed.invoke({"x": "https://example.com"})
        assert "Do NOT treat scraped numbers as verified data" in result

    def test_non_string_results_not_tagged(self):
        """Non-string tool results should pass through without tagging."""
        from prax.agent.action_policy import SourceReliability
        from prax.agent.governed_tool import _tag_result
        assert _tag_result(42, SourceReliability.INFORMATIONAL) == 42
        assert _tag_result(None, SourceReliability.INFORMATIONAL) is None
        assert _tag_result(["a", "b"], SourceReliability.VERIFIED) == ["a", "b"]


class TestToolRegistryIntegration:
    def test_get_registered_tools_returns_governed(self):
        """Verify that tool_registry wraps tools with governance."""
        from prax.agent.tool_registry import get_registered_tools
        tools = get_registered_tools()
        assert len(tools) > 0
        for t in tools:
            assert isinstance(t, StructuredTool), (
                f"Tool {t.name} is {type(t).__name__}, expected StructuredTool (governed)"
            )
