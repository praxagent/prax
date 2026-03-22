import importlib

from langchain_core.tools import tool


def test_tool_registry_round_trip():
    registry = importlib.reload(importlib.import_module('prax.agent.tool_registry'))

    @tool
    def custom_tool() -> str:
        """Return test output."""
        return "ok"

    registry.register_tool(custom_tool)
    tools = registry.get_registered_tools()
    assert custom_tool in tools

    registry.clear_tools()
    assert custom_tool not in registry.get_registered_tools()
