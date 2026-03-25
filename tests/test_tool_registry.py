import importlib

from langchain_core.tools import tool

# OpenAI enforces a hard cap of 128 tools per request.
# This limit is checked at CI time so we never ship a build that breaks in prod.
OPENAI_MAX_TOOLS = 128


def test_tool_registry_round_trip():
    registry = importlib.reload(importlib.import_module('prax.agent.tool_registry'))

    @tool
    def custom_tool() -> str:
        """Return test output."""
        return "ok"

    registry.register_tool(custom_tool)
    tools = registry.get_registered_tools()
    # Tools are wrapped with governance, so check by name rather than identity.
    tool_names = [t.name for t in tools]
    assert "custom_tool" in tool_names

    registry.clear_tools()
    after_names = [t.name for t in registry.get_registered_tools()]
    assert "custom_tool" not in after_names


def test_tool_count_stays_under_openai_limit():
    """Guard rail: the total tool count must stay ≤ 128 (OpenAI's max).

    If this test fails you added tools that push us over the limit.
    Fix options:
      1. Gate rarely-used tools behind a settings flag.
      2. Move internal tools out of the main agent (see build_codegen_tools_for_main_agent).
      3. Consolidate similar tools into one with an 'action' parameter.
    """
    from prax.agent.tools import build_default_tools
    from prax.plugins.loader import get_plugin_loader

    default_tools = build_default_tools()
    plugin_tools = get_plugin_loader().get_tools()
    total = len(default_tools) + len(plugin_tools)

    assert total <= OPENAI_MAX_TOOLS, (
        f"Total tool count ({total}) exceeds OpenAI limit ({OPENAI_MAX_TOOLS}). "
        f"Default: {len(default_tools)}, Plugins: {len(plugin_tools)}. "
        f"See test docstring for fix options."
    )


def test_no_duplicate_tool_names():
    """Every tool name must be unique — duplicates cause silent overwrites."""
    from prax.agent.tools import build_default_tools
    from prax.plugins.loader import get_plugin_loader

    all_tools = build_default_tools() + get_plugin_loader().get_tools()
    names = [t.name for t in all_tools]
    dupes = [n for n in names if names.count(n) > 1]
    assert not dupes, f"Duplicate tool names: {sorted(set(dupes))}"
