"""Tests for the browser spoke agent."""
from unittest.mock import patch


def test_build_spoke_tools_returns_delegate():
    """The browser spoke exports exactly one delegation tool."""
    from prax.agent.spokes.browser import build_spoke_tools

    tools = build_spoke_tools()
    assert len(tools) == 1
    assert tools[0].name == "delegate_browser"


def test_build_tools_includes_cdp_and_playwright():
    """The internal tool set has both CDP and Playwright tools."""
    from prax.agent.spokes.browser.agent import build_tools

    tools = build_tools()
    names = {t.name for t in tools}

    # CDP tools
    assert "sandbox_browser_read" in names
    assert "sandbox_browser_act" in names

    # Playwright tools (spot-check)
    assert "browser_navigate" in names
    assert "browser_fill" in names
    assert "browser_click" in names
    assert "browser_credentials" in names


def test_browser_tools_not_on_orchestrator():
    """Browser tools should NOT be in default tools — only delegate_browser."""
    from prax.agent.tools import build_default_tools

    tools = build_default_tools()
    names = {t.name for t in tools}

    # Should have the delegation tool
    assert "delegate_browser" in names

    # Should NOT have direct browser tools
    assert "browser_navigate" not in names
    assert "browser_fill" not in names
    assert "sandbox_browser_read" not in names
    assert "sandbox_browser_act" not in names


def test_all_spoke_tools():
    """build_all_spoke_tools aggregates spokes correctly."""
    from prax.agent.spokes import build_all_spoke_tools

    tools = build_all_spoke_tools()
    names = {t.name for t in tools}
    assert "delegate_browser" in names


def test_delegate_browser_calls_run_spoke():
    """delegate_browser invokes the shared runner with correct params."""
    with patch("prax.agent.spokes.browser.agent.run_spoke") as mock_run:
        mock_run.return_value = "page content here"
        from prax.agent.spokes.browser.agent import delegate_browser

        result = delegate_browser.invoke({"task": "open example.com and read it"})

        assert result == "page content here"
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert kwargs["config_key"] == "subagent_browser"
        assert kwargs["role_name"] == "Browser Agent"
        assert kwargs["channel"] == "browser"
        assert "open example.com" in kwargs["task"]


def test_subagent_category_browser_falls_back_to_defaults():
    """delegate_task(category='browser') falls back to default tools.

    Browser-specific tools live in the dedicated spoke (delegate_browser),
    not in the generic subagent category path.  This is by design — the
    comment in subagent.py explicitly excludes spoke categories from
    _CATEGORY_BUILDERS.
    """
    from prax.agent.subagent import _get_tools_for_category

    tools = _get_tools_for_category("browser")
    names = {t.name for t in tools}

    # Falls back to the default research-oriented tool set
    assert "background_search_tool" in names
    assert "fetch_url_content" in names

    # Browser-specific tools should NOT be here — they're on the spoke
    assert "sandbox_browser_read" not in names
    assert "browser_navigate" not in names


def test_spoke_runner_handles_no_tools():
    """run_spoke returns a clear message when no tools are available."""
    from prax.agent.spokes._runner import run_spoke

    with patch("prax.agent.spokes._runner.set_role_status", create=True):
        result = run_spoke(
            task="test",
            system_prompt="test",
            tools=[],
            config_key="subagent_test",
            role_name="Test",
        )
    assert "No tools available" in result


def test_spoke_runner_preserves_structured_tool_evidence():
    """Evidence-bearing nested tool output can be carried to the parent."""
    from langchain_core.messages import AIMessage, ToolMessage

    from prax.agent.spokes._runner import _append_preserved_tool_results

    result = _append_preserved_tool_results(
        "Clear and mild in Los Angeles this morning.",
        [
            ToolMessage(
                content=(
                    "VERIFIED_WEATHER\n"
                    "location: Los Angeles, California, United States\n"
                    "temperature: 63.8 °F\n"
                    "sources: https://api.open-meteo.com/v1/forecast"
                ),
                name="environment_current_weather",
                tool_call_id="weather",
            ),
            AIMessage(content="Clear and mild in Los Angeles this morning."),
        ],
        ("VERIFIED_WEATHER",),
    )

    assert "Clear and mild" in result
    assert "[Tool evidence preserved for audit]" in result
    assert "VERIFIED_WEATHER" in result
    assert "api.open-meteo.com" in result


def test_spoke_runner_does_not_duplicate_preserved_evidence():
    from langchain_core.messages import ToolMessage

    from prax.agent.spokes._runner import _append_preserved_tool_results

    evidence = "VERIFIED_WEATHER\nsources: https://api.open-meteo.com/v1/forecast"
    result = _append_preserved_tool_results(
        f"Summary\n\n{evidence}",
        [ToolMessage(content=evidence, name="environment_current_weather", tool_call_id="weather")],
        ("VERIFIED_WEATHER",),
    )

    assert result.count("VERIFIED_WEATHER") == 1
