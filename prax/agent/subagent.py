"""Sub-agent delegation via LangGraph — lets the primary agent hand off tasks."""
from __future__ import annotations

import concurrent.futures
import contextvars
import logging

from langchain.agents import create_agent as create_react_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from prax.agent.llm_factory import build_llm
from prax.settings import settings

logger = logging.getLogger(__name__)

# Tool category imports — deferred to avoid circular imports at module level.
# Categories that have dedicated spokes (browser, sandbox, finetune) are NOT
# listed here — use delegate_browser, delegate_sandbox, delegate_finetune instead.
_CATEGORY_BUILDERS: dict[str, str] = {
    "research": "prax.agent.tools",
    "workspace": "prax.agent.workspace_tools",
    "scheduler": "prax.agent.scheduler_tools",
    "codegen": "prax.agent.codegen_tools",
}

# Spoke name → callable that accepts (task: str) -> str.
# Used by delegate_parallel for concurrent spoke execution.
_SPOKE_DELEGATES: dict[str, str] = {
    "browser": "prax.agent.spokes.browser.agent.delegate_browser",
    "content": "prax.agent.spokes.content.agent.delegate_content_editor",
    "finetune": "prax.agent.spokes.finetune.agent.delegate_finetune",
    "knowledge": "prax.agent.spokes.knowledge.agent.delegate_knowledge",
    "sandbox": "prax.agent.spokes.sandbox.agent.delegate_sandbox",
    "sysadmin": "prax.agent.spokes.sysadmin.agent.delegate_sysadmin",
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
    # Try category-specific name first, then generic spoke convention.
    builder_fn = getattr(mod, f"build_{category}_tools", None) or getattr(mod, "build_tools", None)
    if builder_fn:
        return builder_fn()
    return [background_search_tool, fetch_url_content, get_current_datetime]


def _auto_advance_plan() -> None:
    """Mark the next incomplete plan step as done after a successful delegation.

    This is the reliable signaling mechanism — instead of depending on the LLM
    to call agent_step_done(), the framework auto-advances the plan whenever a
    delegation tool completes successfully.  Steps are advanced sequentially
    (the first incomplete step gets marked done).
    """
    try:
        from prax.agent.user_context import current_user_id
        from prax.services.workspace_service import complete_plan_step, read_plan

        uid = current_user_id.get()
        if not uid:
            return

        plan = read_plan(uid)
        if not plan:
            return

        for step in plan.get("steps", []):
            if not step.get("done"):
                complete_plan_step(uid, step["step"])
                logger.info(
                    "Auto-advanced plan step %d after delegation: %s",
                    step["step"], step.get("description", "")[:60],
                )
                return
    except Exception:
        pass  # Best-effort — don't break the delegation return.


def _run_subagent(task: str, category: str) -> str:
    """Execute a single sub-agent run and return its textual result.

    This is the shared implementation used by both :func:`delegate_task` and
    :func:`delegate_parallel`.
    """
    from prax.agent.trace import GraphCallbackHandler, build_identity_context, start_span

    span = start_span(category, category)

    logger.info("Sub-agent delegated [%s]: %s", category, task[:80])

    # Route engineering-related work to TeamWork.
    _engineering_categories = {"sandbox", "codegen", "workspace"}
    if category in _engineering_categories:
        from prax.services.teamwork_hooks import push_live_output, set_role_status
        set_role_status("Executor", "working")
        push_live_output("Executor", f"[{category}] Starting: {task[:120]}\n", status="running", append=False)

    tools = _get_tools_for_category(category)
    if not tools:
        span.end(status="failed", summary="No tools available")
        return f"No tools available for category '{category}'."

    from prax.plugins.llm_config import get_component_config
    cfg = get_component_config(f"subagent_{category}")
    llm = build_llm(
        provider=cfg.get("provider"),
        model=cfg.get("model"),
        temperature=cfg.get("temperature"),
        tier=cfg.get("tier") or "low",
    )
    subgraph = create_react_agent(llm, tools)

    identity = build_identity_context(category)

    # Metacognitive injection: inject known failure patterns for this spoke
    metacognitive_hint = ""
    try:
        from prax.agent.metacognitive import get_metacognitive_store
        metacognitive_hint = get_metacognitive_store().get_prompt_injection(
            f"subagent_{category}"
        )
    except Exception:
        pass

    system_msg = (
        f"You are a focused sub-agent of {settings.agent_name}. "
        f"Your task category is '{category}'. "
        "Complete the task below using your tools, then give a clear, "
        "concise summary of what you found or did. "
        "Do NOT ask follow-up questions — just do the work.\n\n"
        f"## Execution Context\n{identity}"
        f"{metacognitive_hint}"
    )

    # Set component context for earned trust and autonomy-aware limits.
    from prax.agent.autonomy import get_recursion_limit
    from prax.agent.user_context import current_component
    current_component.set(f"subagent_{category}")

    _graph_cb = GraphCallbackHandler(
        parent_span_id=span.span_id,
        graph=span.ctx.graph,
        trace_id=span.trace_id,
    )

    try:
        result = subgraph.invoke(
            {"messages": [
                SystemMessage(content=system_msg),
                HumanMessage(content=task),
            ]},
            config={
                "recursion_limit": get_recursion_limit(30),
                "callbacks": [_graph_cb],
            },
        )
    except Exception as exc:
        logger.warning("Sub-agent [%s] failed: %s", category, exc, exc_info=True)

        # Multi-perspective error analysis for structured recovery context
        try:
            from prax.agent.error_recovery import analyze_tool_failure
            analysis = analyze_tool_failure(
                tool_name=f"subagent_{category}",
                error_message=str(exc),
                context=task,
            )
            recovery_hint = analysis.best_suggestion
            logger.info(
                "Error recovery suggestion for %s: %s",
                category, recovery_hint[:120],
            )
        except Exception:
            recovery_hint = ""

        # Record failure pattern for metacognitive learning
        try:
            from prax.agent.metacognitive import get_metacognitive_store
            error_type = type(exc).__name__
            get_metacognitive_store().record_failure(
                component=f"subagent_{category}",
                pattern_id=f"{error_type}_{category}",
                description=f"{error_type}: {str(exc)[:100]}",
                category=category,
                compensating_instruction=recovery_hint or f"Watch for {error_type} in {category} tasks.",
            )
        except Exception:
            pass

        span.end(status="failed", summary=str(exc)[:200])
        return f"Sub-agent failed: {exc}"

    # Log the sub-agent's tool call trace for debugging.
    from langchain_core.messages import ToolMessage
    tool_count = 0
    live_lines: list[str] = []
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_count += 1
                logger.info("Sub-agent [%s] tool: %s(%s)", category, tc.get("name"), str(tc.get("args", {}))[:80])
                live_lines.append(f"  → {tc.get('name')}({str(tc.get('args', {}))[:80]})")
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:200]
            if "error" in preview.lower() or "fail" in preview.lower():
                logger.warning("Sub-agent [%s] tool error [%s]: %s", category, msg.name, preview)
                live_lines.append(f"  ✗ {msg.name}: {preview[:120]}")
            else:
                logger.info("Sub-agent [%s] result [%s]: %s", category, msg.name, preview[:120])
                live_lines.append(f"  ✓ {msg.name}: {preview[:120]}")

    # Push tool call log to TeamWork live output for engineering categories
    if category in _engineering_categories and live_lines:
        from prax.services.teamwork_hooks import push_live_output
        push_live_output("Executor", "\n".join(live_lines) + "\n")

    # Extract the final AI response.
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Sub-agent [%s] completed: %s", category, msg.content[:80])
            span.end(status="completed", summary=msg.content[:200], tool_calls=tool_count)
            # Route engineering work to the #engineering channel.
            if category in _engineering_categories:
                from prax.services.teamwork_hooks import post_to_channel, push_live_output, set_role_status
                set_role_status("Executor", "idle")
                post_to_channel("engineering", msg.content[:3000], agent_name="Executor")
                push_live_output("Executor", f"\n[{category}] completed: {msg.content[:200]}\n", status="completed")
            # Auto-advance the plan — mark the next incomplete step as done
            # so the orchestrator doesn't loop trying to re-delegate.
            _auto_advance_plan()
            return msg.content

    if category in _engineering_categories:
        from prax.services.teamwork_hooks import push_live_output, set_role_status
        set_role_status("Executor", "idle")
        push_live_output("Executor", f"\n[{category}] completed (no output)\n", status="completed")
    span.end(status="completed", summary="No output produced", tool_calls=tool_count)
    return "Sub-agent completed but produced no output."


@tool
def delegate_task(task: str, category: str = "research") -> str:
    """Hand off an independent subtask to a focused sub-agent that runs its own
    tool loop and returns a summary.

    Use this for research-heavy or multi-step work that doesn't need your direct
    involvement at each step.  The sub-agent has access to a focused set of tools
    based on the category.

    For specialized work, prefer the dedicated spoke agents instead:
    delegate_browser, delegate_sandbox, delegate_sysadmin, delegate_finetune,
    delegate_content_editor, delegate_knowledge.

    Args:
        task: A clear, self-contained description of what the sub-agent should do.
              Include all context it needs — it cannot see your conversation history.
        category: Which tool set the sub-agent gets.  One of:
                  "research" (web search, URL fetch, PDFs — default),
                  "workspace" (file management, todos),
                  "scheduler" (cron jobs, reminders),
                  "codegen" (self-improvement PRs).
    """
    return _run_subagent(task, category)


_PARALLEL_TIMEOUT_SECONDS = 120


def _run_spoke_or_subagent(spec: dict) -> str:
    """Route a parallel task to a spoke or generic sub-agent.

    If ``spoke`` is set, invokes the named spoke's delegate function directly.
    Otherwise falls back to ``_run_subagent`` with the given category.

    Each task gets a named span in the execution graph for traceability.
    """
    import importlib

    from prax.agent.trace import start_span

    task_desc = spec.get("task", "")
    spoke_name = spec.get("spoke")
    task_name = spec.get("name", spoke_name or spec.get("category", "research"))
    spoke_or_cat = spoke_name or spec.get("category", "research")

    span = start_span(task_name, spoke_or_cat)

    try:
        if spoke_name:
            delegate_path = _SPOKE_DELEGATES.get(spoke_name)
            if not delegate_path:
                result = f"Unknown spoke: '{spoke_name}'. Available: {', '.join(_SPOKE_DELEGATES)}"
                span.end(status="failed", summary=result)
                return result
            module_path, func_name = delegate_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            delegate_fn = getattr(mod, func_name)
            # Spokes are @tool-decorated — invoke via .invoke() for LangChain compat.
            result = delegate_fn.invoke({"task": task_desc})
        else:
            category = spec.get("category", "research")
            result = _run_subagent(task_desc, category)

        span.end(status="completed", summary=(result or "")[:200])
        return result
    except Exception as exc:
        span.end(status="failed", summary=str(exc)[:200])
        raise


@tool
def delegate_parallel(tasks: list[dict]) -> str:
    """Run multiple independent tasks in parallel — across spokes and sub-agents.

    Each task is a dict with:
        - task: str — self-contained description with all needed context
        - spoke: str — (optional) spoke name: browser, content, finetune,
          knowledge, sandbox, sysadmin.  Routes to the dedicated spoke agent.
        - category: str — (optional, if no spoke) sub-agent category:
          research, workspace, scheduler, codegen.
        - name: str — (optional) human-readable identity for this task.
          Auto-generated as ``{spoke_or_category}-{index}`` if not set.

    Use ``spoke`` for specialized work, ``category`` for generic sub-agent tasks.
    If neither is set, defaults to category="research".

    Returns a summary of all results plus an execution graph showing the
    full delegation tree with timing and status.

    Example:
        delegate_parallel([
            {"task": "Search arXiv for TurboQuant and summarize", "category": "research"},
            {"task": "Check all plugins for updates", "spoke": "sysadmin"},
            {"task": "Open example.com and take a screenshot", "spoke": "browser", "name": "screenshot"},
        ])
    """
    if not tasks:
        return "No tasks provided."

    from prax.agent.trace import get_graph_summary, start_span

    span = start_span("parallel", "parallel")

    # Assign names to tasks that don't have one
    for idx, spec in enumerate(tasks):
        if "name" not in spec:
            spoke = spec.get("spoke")
            cat = spec.get("category", "research")
            spec["name"] = f"{spoke or cat}-{idx + 1}"

    logger.info(
        "delegate_parallel: launching %d tasks: %s",
        len(tasks),
        ", ".join(s.get("name", "?") for s in tasks),
    )

    results: list[str] = [""] * len(tasks)

    # Copy the current context (ContextVars: user_id, channel_id, active_view, etc.)
    # so worker threads inherit them.  Without this, ContextVars default to None in
    # thread-pool workers, breaking user resolution and browser session lookup.
    ctx = contextvars.copy_context()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        future_to_idx: dict[concurrent.futures.Future, int] = {}
        for idx, spec in enumerate(tasks):
            future = pool.submit(ctx.run, _run_spoke_or_subagent, spec)
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

    parts: list[str] = []
    for idx, result_text in enumerate(results, start=1):
        task_name = tasks[idx - 1].get("name", f"task-{idx}")
        task_label = tasks[idx - 1].get("task", "(unknown)")[:80]
        spoke_or_cat = tasks[idx - 1].get("spoke") or tasks[idx - 1].get("category", "research")
        parts.append(f"--- {task_name} [{spoke_or_cat}]: {task_label} ---\n{result_text}")

    summary = "\n\n".join(parts)

    # Append execution graph for the governing agent
    graph = get_graph_summary()
    if graph and "No active trace" not in graph:
        summary += f"\n\n## Execution Graph\n{graph}"

    span.end(status="completed", summary=f"{len(tasks)} tasks completed")
    logger.info("delegate_parallel: all %d tasks complete", len(tasks))
    return summary


def build_subagent_tools() -> list:
    """Return the list of sub-agent tools to register with the main agent."""
    return [delegate_task, delegate_parallel]
