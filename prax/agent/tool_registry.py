"""Tool registry — aggregates built-in tools, custom plugins, and extras.

Every tool passes through the governance wrapper before reaching the agent.
This is the single choke point for risk classification, audit logging, and
confirmation gating.  See :mod:`prax.agent.governed_tool` for details.
"""
from __future__ import annotations

from langchain_core.tools import BaseTool

from prax.agent.governed_tool import wrap_with_governance
from prax.agent.tools import build_default_tools
from prax.plugins.loader import get_plugin_loader

_registered: list[BaseTool] = []


def register_tool(tool: BaseTool) -> None:
    _registered.append(tool)


def clear_tools() -> None:
    _registered.clear()


def get_registered_tools() -> list[BaseTool]:
    """Return all tools: built-in + plugin-provided + manually registered.

    Every tool is wrapped with governance (risk classification + audit
    logging) before being handed to the agent.
    """
    loader = get_plugin_loader()
    raw = build_default_tools() + loader.get_tools() + list(_registered)
    return [wrap_with_governance(t) for t in raw]
