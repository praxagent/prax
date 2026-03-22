"""Sub-agent delegation via LangGraph — lets the primary agent hand off tasks."""
from __future__ import annotations

import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

# Tool category imports — deferred to avoid circular imports at module level.
_CATEGORY_BUILDERS: dict[str, str] = {
    "research": "prax.agent.tools",
    "workspace": "prax.agent.workspace_tools",
    "browser": "prax.agent.browser_tools",
    "scheduler": "prax.agent.scheduler_tools",
    "sandbox": "prax.agent.sandbox_tools",
    "codegen": "prax.agent.codegen_tools",
    "finetune": "prax.agent.finetune_tools",
}


def _get_tools_for_category(category: str) -> list:
    """Return the tool list for a given category."""
    import importlib

    from prax.agent.tools import (
        background_search_tool,
        fetch_url_content,
        get_current_datetime,
    )
    from prax.plugins.loader import get_plugin_loader

    if category == "research":
        # Core tools + any plugin-provided tools (NPR, PDF, web summary, etc.).
        return [background_search_tool, fetch_url_content, get_current_datetime] + \
            get_plugin_loader().get_tools()

    builder_module = _CATEGORY_BUILDERS.get(category)
    if not builder_module:
        return [background_search_tool, fetch_url_content, get_current_datetime]

    mod = importlib.import_module(builder_module)
    builder_fn = getattr(mod, f"build_{category}_tools", None)
    if builder_fn:
        return builder_fn()
    return [background_search_tool, fetch_url_content, get_current_datetime]


@tool
def delegate_task(task: str, category: str = "research") -> str:
    """Hand off an independent subtask to a focused sub-agent that runs its own
    tool loop and returns a summary.

    Use this for research-heavy or multi-step work that doesn't need your direct
    involvement at each step.  The sub-agent has access to a focused set of tools
    based on the category.

    Args:
        task: A clear, self-contained description of what the sub-agent should do.
              Include all context it needs — it cannot see your conversation history.
        category: Which tool set the sub-agent gets.  One of:
                  "research" (web search, URL fetch, PDFs — default),
                  "workspace" (file management, notes, todos),
                  "browser" (Playwright automation),
                  "scheduler" (cron jobs, reminders),
                  "sandbox" (Docker code execution),
                  "codegen" (self-improvement PRs),
                  "finetune" (model training).
    """
    logger.info("Sub-agent delegated [%s]: %s", category, task[:80])

    tools = _get_tools_for_category(category)
    if not tools:
        return f"No tools available for category '{category}'."

    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config(f"subagent_{category}")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
    )
    subgraph = create_react_agent(llm, tools)

    system_msg = (
        f"You are a focused sub-agent of {settings.agent_name}. "
        f"Your task category is '{category}'. "
        "Complete the task below using your tools, then give a clear, "
        "concise summary of what you found or did. "
        "Do NOT ask follow-up questions — just do the work."
    )

    try:
        result = subgraph.invoke(
            {"messages": [
                SystemMessage(content=system_msg),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": 60},
        )
    except Exception as exc:
        logger.warning("Sub-agent [%s] failed: %s", category, exc, exc_info=True)
        return f"Sub-agent failed: {exc}"

    # Log the sub-agent's tool call trace for debugging.
    from langchain_core.messages import ToolMessage
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                logger.info("Sub-agent [%s] tool: %s(%s)", category, tc.get("name"), str(tc.get("args", {}))[:80])
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:200]
            if "error" in preview.lower() or "fail" in preview.lower():
                logger.warning("Sub-agent [%s] tool error [%s]: %s", category, msg.name, preview)
            else:
                logger.info("Sub-agent [%s] result [%s]: %s", category, msg.name, preview[:120])

    # Extract the final AI response.
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Sub-agent [%s] completed: %s", category, msg.content[:80])
            return msg.content

    return "Sub-agent completed but produced no output."


def build_subagent_tools() -> list:
    """Return the list of sub-agent tools to register with the main agent."""
    return [delegate_task]
