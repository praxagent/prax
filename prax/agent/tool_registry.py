"""Tool registry — aggregates built-in tools, custom plugins, and extras."""
from __future__ import annotations

from langchain_core.tools import BaseTool

from prax.agent.tools import build_default_tools
from prax.plugins.loader import get_plugin_loader

_registered: list[BaseTool] = []


def register_tool(tool: BaseTool) -> None:
    _registered.append(tool)


def clear_tools() -> None:
    _registered.clear()


def get_registered_tools() -> list[BaseTool]:
    """Return all tools: built-in + plugin-provided + manually registered."""
    loader = get_plugin_loader()
    return build_default_tools() + loader.get_tools() + list(_registered)
