"""Tests for the browser spoke agent."""
from unittest.mock import MagicMock, patch


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
    assert "browser_open" in names
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
    assert "browser_open" not in names
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


def test_subagent_category_browser_uses_spoke_tools():
    """The generic delegate_task(category='browser') uses spoke tools."""
    from prax.agent.subagent import _get_tools_for_category

    tools = _get_tools_for_category("browser")
    names = {t.name for t in tools}

    assert "sandbox_browser_read" in names
    assert "browser_open" in names


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
