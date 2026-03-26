"""Sub-agent delegation via LangGraph — lets the primary agent hand off tasks."""
from __future__ import annotations

import concurrent.futures
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


def _run_subagent(task: str, category: str) -> str:
    """Execute a single sub-agent run and return its textual result.

    This is the shared implementation used by both :func:`delegate_task` and
    :func:`delegate_parallel`.
    """
    logger.info("Sub-agent delegated [%s]: %s", category, task[:80])

    # Route engineering-related work to TeamWork.
    _engineering_categories = {"sandbox", "codegen", "workspace"}
    if category in _engineering_categories:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status("Executor", "working")

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
            # Route engineering work to the #engineering channel.
            if category in _engineering_categories:
                from prax.services.teamwork_hooks import set_role_status, post_to_channel
                set_role_status("Executor", "idle")
                post_to_channel("engineering", msg.content[:3000], agent_name="Executor")
            return msg.content

    if category in _engineering_categories:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status("Executor", "idle")
    return "Sub-agent completed but produced no output."


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
    return _run_subagent(task, category)


_PARALLEL_TIMEOUT_SECONDS = 120


@tool
def delegate_parallel(tasks: list[dict]) -> str:
    """Run multiple independent sub-agent tasks in parallel.

    Each task is a dict with:
        - task: str — self-contained description with all needed context
        - category: str — tool set category (research, workspace, browser, sandbox, etc.)

    Returns a numbered summary of all results.

    Example:
        delegate_parallel([
            {"task": "Search arXiv for the TurboQuant paper and summarize it", "category": "research"},
            {"task": "Fetch https://research.google/blog/turboquant and extract key points", "category": "research"},
        ])
    """
    if not tasks:
        return "No tasks provided."

    logger.info("delegate_parallel: launching %d sub-agents", len(tasks))

    # Submit all tasks to a thread pool so they run concurrently.
    results: list[str] = [""] * len(tasks)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        future_to_idx: dict[concurrent.futures.Future, int] = {}
        for idx, spec in enumerate(tasks):
            task_desc = spec.get("task", "")
            category = spec.get("category", "research")
            future = pool.submit(_run_subagent, task_desc, category)
            future_to_idx[future] = idx

        done, not_done = concurrent.futures.wait(
            future_to_idx.keys(),
            timeout=_PARALLEL_TIMEOUT_SECONDS,
        )

        for future in done:
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.warning("delegate_parallel task %d failed: %s", idx + 1, exc, exc_info=True)
                results[idx] = f"Task failed: {exc}"

        for future in not_done:
            idx = future_to_idx[future]
            future.cancel()
            results[idx] = "Task timed out."

    # Build a numbered summary preserving original task order.
    parts: list[str] = []
    for idx, result_text in enumerate(results, start=1):
        task_label = tasks[idx - 1].get("task", "(unknown)")[:80]
        parts.append(f"--- Task {idx}: {task_label} ---\n{result_text}")

    summary = "\n\n".join(parts)
    logger.info("delegate_parallel: all %d tasks complete", len(tasks))
    return summary


def build_subagent_tools() -> list:
    """Return the list of sub-agent tools to register with the main agent."""
    return [delegate_task, delegate_parallel]
