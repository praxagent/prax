"""Tests for prax.agent.action_policy."""
from __future__ import annotations

# Reference to the module for the coverage enforcement test.
import prax.agent.action_policy as _mod
from prax.agent.action_policy import (
    TOOL_CAPABILITIES,
    RiskLevel,
    SourceReliability,
    get_risk_level,
    get_tool_capability,
    log_action,
    requires_confirmation,
    risk_tool,
)

# ── classification ───────────────────────────────────────────────────


def test_known_tools_classified():
    assert get_risk_level("sandbox_execute") is RiskLevel.HIGH
    assert get_risk_level("workspace_send_file") is RiskLevel.HIGH
    assert get_risk_level("browser_open") is RiskLevel.MEDIUM
    assert get_risk_level("arxiv_search") is RiskLevel.MEDIUM
    assert get_risk_level("note_create") is RiskLevel.MEDIUM
    # LOW tools are anything not explicitly mapped — but let's verify
    # a clearly read-only name still falls through to the default.


def test_unknown_tool_defaults_to_medium():
    assert get_risk_level("totally_made_up_tool") is RiskLevel.MEDIUM
    assert get_risk_level("") is RiskLevel.MEDIUM


# ── confirmation gating ──────────────────────────────────────────────


def test_high_risk_requires_confirmation():
    assert requires_confirmation("sandbox_execute") is True
    assert requires_confirmation("plugin_write") is True
    assert requires_confirmation("self_improve_deploy") is True


def test_low_risk_no_confirmation():
    # LOW-risk tools are not in the map, so they get MEDIUM by default.
    # Explicitly test a MEDIUM tool — it should NOT require confirmation.
    assert requires_confirmation("browser_open") is False
    assert requires_confirmation("arxiv_search") is False
    # Unknown tools default to MEDIUM, so no confirmation either.
    assert requires_confirmation("workspace_read") is False


# ── audit logging ────────────────────────────────────────────────────


def test_log_action_structure():
    entry = log_action("browser_click", RiskLevel.HIGH, {"selector": "#btn"})
    assert set(entry.keys()) == {"timestamp", "tool_name", "risk", "args", "result"}
    assert entry["tool_name"] == "browser_click"
    assert entry["risk"] == "high"
    assert entry["result"] is None


def test_log_action_truncates():
    long_args = {"data": "x" * 500}
    long_result = "y" * 500
    entry = log_action("sandbox_execute", RiskLevel.HIGH, long_args, long_result)
    assert len(entry["args"]) <= 203  # 200 + "..."
    assert entry["args"].endswith("...")
    assert len(entry["result"]) <= 203
    assert entry["result"].endswith("...")


# ── risk_tool decorator ──────────────────────────────────────────────


def test_risk_tool_decorator_creates_valid_tool():
    from langchain_core.tools import StructuredTool

    @risk_tool(risk=RiskLevel.HIGH)
    def my_test_tool(x: str) -> str:
        """A test tool."""
        return f"result:{x}"

    assert isinstance(my_test_tool, StructuredTool)
    assert my_test_tool.name == "my_test_tool"
    assert my_test_tool.invoke({"x": "hello"}) == "result:hello"


def test_risk_tool_attaches_risk_level():

    @risk_tool(risk=RiskLevel.HIGH)
    def high_tool(x: str) -> str:
        """High risk tool."""
        return x

    assert hasattr(high_tool, "_risk_level")
    assert high_tool._risk_level is RiskLevel.HIGH

    @risk_tool(risk=RiskLevel.MEDIUM)
    def med_tool(x: str) -> str:
        """Medium risk tool."""
        return x

    assert med_tool._risk_level is RiskLevel.MEDIUM


# ── tool capability metadata ──────────────────────────────────────────


def test_source_reliability_enum_values():
    assert SourceReliability.VERIFIED.value == "verified"
    assert SourceReliability.INDICATIVE.value == "indicative"
    assert SourceReliability.INFORMATIONAL.value == "informational"


def test_background_search_is_informational():
    cap = get_tool_capability("background_search_tool")
    assert cap is not None
    assert cap["reliability"] is SourceReliability.INFORMATIONAL
    assert "live prices" in cap["not_good_for"]


def test_flight_search_is_verified():
    cap = get_tool_capability("flight_search")
    assert cap is not None
    assert cap["reliability"] is SourceReliability.VERIFIED


def test_unknown_tool_returns_none():
    assert get_tool_capability("totally_made_up_tool") is None


def test_all_capabilities_have_required_keys():
    required_keys = {"reliability", "good_for", "not_good_for", "epistemic_note"}
    for name, cap in TOOL_CAPABILITIES.items():
        missing = required_keys - set(cap.keys())
        assert missing == set(), f"{name} missing keys: {missing}"


def test_governed_wrapper_uses_tool_metadata():
    from prax.agent.governed_tool import _audit_buffer, _high_risk_seen, wrap_with_governance

    _audit_buffer.clear()
    _high_risk_seen.clear()

    @risk_tool(risk=RiskLevel.HIGH)
    def my_gated_tool(x: str = "") -> str:
        """A gated tool."""
        return f"executed:{x}"

    governed = wrap_with_governance(my_gated_tool)
    result = governed.invoke({"x": "test"})
    # Should be blocked on first call because risk is HIGH
    assert "HIGH risk" in result
    assert "confirm" in result.lower()


# ── coverage enforcement ────────────────────────────────────────────


def test_high_risk_tools_have_metadata():
    """Every tool in the central _HIGH fallback map must also carry
    ``_risk_level`` metadata at its definition site.

    If this test fails, a HIGH-risk tool was added to the central map
    but not annotated with ``@risk_tool(risk=RiskLevel.HIGH)`` at its
    definition.  Fix by replacing ``@tool`` with
    ``@risk_tool(risk=RiskLevel.HIGH)`` on the tool function.
    """
    from prax.agent.tools import build_default_tools
    from prax.plugins.loader import get_plugin_loader

    # Get ALL raw (unwrapped) tools.
    all_tools = build_default_tools() + get_plugin_loader().get_tools()
    tool_by_name = {t.name: t for t in all_tools}

    # _HIGH is the central fallback map of HIGH-risk tool names.
    _HIGH = _mod._HIGH

    missing_metadata = []
    for name in sorted(_HIGH):
        t = tool_by_name.get(name)
        if t is None:
            continue  # Tool might be gated behind a settings flag.
        if not hasattr(t, "_risk_level"):
            missing_metadata.append(name)

    assert missing_metadata == [], (
        f"HIGH-risk tools without @risk_tool metadata: {missing_metadata}. "
        f"Replace @tool with @risk_tool(risk=RiskLevel.HIGH) at the definition site."
    )
