"""Plugin spoke — route manifest-declared end-user plugin tools."""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke

PLUGIN_ROUTES = frozenset({
    "artifact",
    "media",
    "utility",
    "vision",
    "workspace",
})

SYSTEM_PROMPT = """\
You are the Plugin Agent for Prax. Your job is to execute end-user tasks using
installed plugin tools whose manifests declare task routes such as artifact,
media, vision, utility, or workspace.

Rules:
- Use the most specific plugin tool available for the user's request.
- Do not manage, install, modify, or activate plugins; those tasks belong to
  the sysadmin agent.
- Do not use general sandbox execution to recreate a capability when a plugin
  tool matches the request.
- For narrated video or slide generation, call the presentation plugin tool.
  Do not report completion unless the tool result clearly says the artifact
  was produced. If the tool reports missing dependencies, permissions, or a
  concrete error, return that exact blocker.
- Be concise and include the resulting artifact path, URL, or tool result.
"""


def build_tools() -> list:
    """Return manifest-routed end-user plugin tools."""
    from prax.plugins.loader import get_plugin_loader

    return get_plugin_loader().get_tools_for_routes(PLUGIN_ROUTES)


@tool
def delegate_plugins(task: str) -> str:
    """Delegate an end-user plugin task to the plugin spoke.

    Use this for installed plugin capabilities such as presentation/video
    generation, media tools, vision/OCR plugins, and other manifest-declared
    artifact or utility tools. Do not use it for plugin management; use
    delegate_sysadmin for install/update/list/status/config/source tasks.

    Args:
        task: A self-contained description of the plugin task to execute.
    """
    return run_spoke(
        task=task,
        system_prompt=SYSTEM_PROMPT,
        tools=build_tools(),
        config_key="subagent_plugins",
        role_name="Plugin Agent",
        recursion_limit=40,
    )


def build_spoke_tools() -> list:
    """Return delegation tools for the orchestrator."""
    return [delegate_plugins]
