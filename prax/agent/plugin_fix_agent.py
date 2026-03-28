"""Plugin fix/create sub-agent — handles the full plugin lifecycle.

Prax delegates to this agent when a plugin needs to be created, fixed, or
improved.  The agent can read the existing source, use the sandbox to write
and test a patched version, then activate it — all in one shot.
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

_PLUGIN_AGENT_PROMPT = """\
You are a plugin engineering agent for {agent_name}.  Your job is to create,
fix, and improve tool plugins.

## How plugins work
Plugins are Python modules at ``custom/<name>/plugin.py`` with:
- PLUGIN_VERSION and PLUGIN_DESCRIPTION constants
- @tool decorated functions (from langchain_core.tools)
- A register() function that returns a list of tool functions

## Available tools
- **source_read / source_list**: Read any file in the Prax codebase, including
  existing plugins under prax/plugins/tools/.
- **plugin_read**: Read a custom plugin's source.
- **plugin_catalog**: See all available plugins (built-in and custom).
- **sandbox_start / sandbox_message / sandbox_review**: Use the sandbox
  (OpenCode coding agent) to write and test plugin code.  The Prax source
  is available at /source/ in the sandbox.
- **plugin_write**: Write a plugin (auto-tests in sandbox, creates folder).
- **plugin_activate**: Make a plugin live.
- **plugin_test**: Test a plugin without activating.
- **plugin_rollback**: Revert a plugin to its previous version.
- **plugin_status**: Check a plugin's health.

## Workflow
1. **Understand**: Read the existing plugin source (source_read or plugin_read)
   and the error or requirement.
2. **Write the fix**: For simple fixes, write the code directly with plugin_write.
   For complex ones, use the sandbox — start a session with the current plugin
   source pasted in, describe the fix needed, let OpenCode produce the patched
   code, then use plugin_write with the result.
3. **Test**: plugin_write auto-tests.  If it fails, read the error, fix, retry.
4. **Activate**: Call plugin_activate to make it live.
5. **Report**: Return what you changed, which tools are affected, and whether
   the fix is active.

## API credentials & settings
- **NEVER use os.environ** — the security scanner blocks it.  Use
  ``from prax.settings import settings`` to access configuration.
- Available settings: ``settings.openai_key``, ``settings.vllm_base_url``,
  ``settings.local_model``, and others (read prax/settings.py for the full list).
- For OpenAI API calls, use the standard ``openai.OpenAI(api_key=settings.openai_key)``
  client.  It defaults to ``https://api.openai.com/v1`` — do NOT require a
  separate base URL for standard OpenAI services.
- For vision/image tasks, use OpenAI's vision models (e.g. ``gpt-4o``) via
  the standard OpenAI client with ``settings.openai_key``.

## Rules
- Never write stubs or placeholders that fake success.
- Preserve the full original functionality — only change what's broken.
- Max 3 attempts per fix.  If all fail, report what's wrong clearly.
- Always include PLUGIN_VERSION (increment it) and PLUGIN_DESCRIPTION.
"""


def _build_plugin_agent_tools() -> list:
    """Assemble the tool set for the plugin agent."""
    from prax.agent.plugin_tools import (
        plugin_activate,
        plugin_catalog,
        plugin_read,
        plugin_rollback,
        plugin_status,
        plugin_test,
        plugin_write,
        source_list,
        source_read,
    )
    from prax.agent.sandbox_tools import build_sandbox_tools

    return [
        source_read, source_list,
        plugin_read, plugin_write, plugin_test,
        plugin_activate, plugin_rollback, plugin_status, plugin_catalog,
    ] + build_sandbox_tools()


@tool
def delegate_plugin_fix(task: str) -> str:
    """Delegate a plugin creation, fix, or improvement to the plugin agent.

    The plugin agent can read existing plugin source, use the sandbox to
    write and test patches, and activate the result — all autonomously.

    Use this when:
    - A plugin tool is failing or returning wrong results
    - The user asks you to create a new plugin
    - A built-in plugin needs customization or improvement

    Args:
        task: Detailed description of what needs to be done.  Include the
              plugin name, error messages, and expected behavior.
    """
    logger.info("Plugin agent delegated: %s", task[:100])

    tools = _build_plugin_agent_tools()

    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config("subagent_codegen")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
        tier=cfg.get("tier") or "medium",
    )
    graph = create_react_agent(llm, tools)

    system_msg = _PLUGIN_AGENT_PROMPT.format(agent_name=settings.agent_name)

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=system_msg),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 60},
        )
    except Exception as exc:
        logger.warning("Plugin agent failed: %s", exc, exc_info=True)
        return f"Plugin agent failed: {exc}"

    # Log the sub-agent's tool call trace for debugging.
    from langchain_core.messages import ToolMessage
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                logger.info("Plugin agent tool: %s(%s)", tc.get("name"), str(tc.get("args", {}))[:80])
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:200]
            if "error" in preview.lower() or "fail" in preview.lower():
                logger.warning("Plugin agent tool error [%s]: %s", msg.name, preview)
            else:
                logger.info("Plugin agent result [%s]: %s", msg.name, preview[:120])

    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Plugin agent completed: %s", msg.content[:200])
            return msg.content

    return "Plugin agent completed but produced no output."


def build_plugin_fix_tools() -> list:
    """Return the delegate tool for the main agent."""
    return [delegate_plugin_fix]
