"""Shared delegation engine for spoke agents.

Every spoke uses ``run_spoke()`` to build an LLM, create a ReAct agent,
invoke it, log tool calls, extract the final response, and update TeamWork.

This keeps individual spokes focused on their prompt and tool selection.
"""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from prax.agent.llm_factory import build_llm

logger = logging.getLogger(__name__)


def run_spoke(
    *,
    task: str,
    system_prompt: str,
    tools: list,
    config_key: str,
    default_tier: str = "low",
    role_name: str | None = None,
    channel: str | None = None,
    recursion_limit: int = 80,
) -> str:
    """Execute a spoke agent and return its textual result.

    Parameters
    ----------
    task:
        The self-contained task description (becomes the HumanMessage).
    system_prompt:
        The spoke's system prompt (becomes the SystemMessage).
    tools:
        The LangChain tools available to the spoke.
    config_key:
        LLM routing key, e.g. ``"subagent_browser"``.
    default_tier:
        Fallback model tier when the config doesn't specify one.
    role_name:
        Optional TeamWork role for status updates (e.g. ``"Browser Agent"``).
    channel:
        Optional TeamWork channel to post results to.
    recursion_limit:
        Max tool calls before the agent is forced to stop.
    """
    label = config_key.replace("subagent_", "")
    logger.info("Spoke [%s] delegated: %s", label, task[:120])

    # TeamWork status
    if role_name:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status(role_name, "working")

    if not tools:
        _finish(role_name)
        return f"No tools available for spoke '{label}'."

    # Build LLM from per-component config
    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config(config_key)
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
        tier=cfg.get("tier") or default_tier,
    )

    graph = create_react_agent(llm, tools)

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=system_prompt),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": recursion_limit},
        )
    except Exception as exc:
        logger.warning("Spoke [%s] failed: %s", label, exc, exc_info=True)
        _finish(role_name)
        return f"Spoke agent failed: {exc}"

    # Log tool calls for debugging
    tool_count = _log_tool_calls(result, label)

    # Extract the final AI response
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Spoke [%s] completed (%d tool calls): %s", label, tool_count, msg.content[:120])
            _finish(role_name, channel, msg.content)
            return msg.content

    _finish(role_name)
    return f"Spoke [{label}] completed but produced no output."


def _log_tool_calls(result: dict, label: str) -> int:
    """Log all tool calls and flag errors.  Returns the total call count."""
    tool_count = 0
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_count += 1
                logger.info("Spoke [%s] tool: %s(%s)", label, tc.get("name"), str(tc.get("args", {}))[:80])
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:200]
            if "error" in preview.lower() or "fail" in preview.lower():
                logger.warning("Spoke [%s] tool error [%s]: %s", label, msg.name, preview)
    return tool_count


def _finish(
    role_name: str | None,
    channel: str | None = None,
    content: str | None = None,
) -> None:
    """Set TeamWork role to idle and optionally post to a channel."""
    if role_name:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status(role_name, "idle")
    if channel and content:
        from prax.services.teamwork_hooks import post_to_channel
        post_to_channel(channel, content[:3000], agent_name=role_name or "Agent")
