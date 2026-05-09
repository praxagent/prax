"""Tool registry — aggregates built-in tools, custom plugins, and extras.

Every tool passes through the governance wrapper before reaching the agent.
This is the single choke point for risk classification, audit logging, and
confirmation gating.  See :mod:`prax.agent.governed_tool` for details.
"""
from __future__ import annotations

from langchain_core.tools import BaseTool

from prax.agent.governed_tool import wrap_with_governance
from prax.agent.tools import build_default_tools

_registered: list[BaseTool] = []

# Keep the orchestrator's tool list close to the ~50-tool target while still
# exposing artifact plugins whose absence makes the agent improvise with
# ad hoc sandbox scripts. Most plugin tools remain behind domain spokes.
_ORCHESTRATOR_PLUGIN_ALLOWLIST = frozenset({
    "text_to_presentation",
    "pdf_to_presentation",
})


def register_tool(tool: BaseTool) -> None:
    _registered.append(tool)


def clear_tools() -> None:
    _registered.clear()


def get_registered_tools() -> list[BaseTool]:
    """Return tools for the orchestrator.

    Most plugin-provided tools (arxiv, news, pdf, youtube, web_summary, etc.)
    are accessed via domain spokes such as ``delegate_research``. A small
    allowlist of presentation-generation plugin tools is promoted here because
    they are direct artifact-creation commands; hiding them caused the main
    agent to fall back to ad hoc sandbox scripts instead of using the narrated
    presentation pipeline.

    Every tool is wrapped with governance (risk classification + audit
    logging) before being handed to the agent.
    """
    raw = build_default_tools() + _promoted_plugin_tools() + list(_registered)
    raw = _dedupe_tools(raw)
    return [wrap_with_governance(t) for t in raw]


def _promoted_plugin_tools() -> list[BaseTool]:
    """Return plugin tools safe and useful enough to expose to the main agent."""
    try:
        from prax.plugins.loader import get_plugin_loader
        loader = get_plugin_loader()
        tools = loader.get_tools()
    except Exception:
        return []
    promoted: list[BaseTool] = []
    for tool in tools:
        manifest = loader.get_tool_manifest(tool.name)
        if (
            manifest
            and manifest.orchestrator_exposure == "requested"
            and tool.name in _ORCHESTRATOR_PLUGIN_ALLOWLIST
        ):
            promoted.append(tool)
    return promoted


def _dedupe_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Preserve the first tool by name so plugin promotions cannot duplicate."""
    seen: set[str] = set()
    deduped: list[BaseTool] = []
    for tool in tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        deduped.append(tool)
    return deduped
