"""Shared delegation engine for spoke agents.

Every spoke uses ``run_spoke()`` to build an LLM, create a ReAct agent,
invoke it, log tool calls, extract the final response, and update TeamWork.

This keeps individual spokes focused on their prompt and tool selection.

Features:
- **Read guard**: optional ``pre_check`` callback that can abort before work
  starts (inspired by smux's read-before-act pattern).
- **Trace spans**: every invocation is tracked in the execution graph with
  a chain UUID and named span.
- **Identity injection**: agents receive execution context (trace, depth,
  siblings) in their system prompt for situational awareness.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

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
    pre_check: Callable[[], str | None] | None = None,
    state_context: str | None = None,
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
    pre_check:
        Optional read-guard callback.  Called before the agent starts.
        Return ``None`` to proceed, or a string describing why to abort.
    state_context:
        Optional description of the observed state before execution.
        Injected into the system prompt so the agent knows the starting
        conditions (read-before-act pattern).
    """
    from prax.agent.trace import build_identity_context, start_span

    import time as _time

    label = config_key.replace("subagent_", "")
    span = start_span(label, label)
    _spoke_start = _time.monotonic()

    logger.info("Spoke [%s] delegated: %s", label, task[:120])

    # --- Read guard ---
    if pre_check:
        guard_result = pre_check()
        if guard_result:
            logger.warning("Spoke [%s] pre-check failed: %s", label, guard_result)
            span.end(status="aborted", summary=f"Pre-check: {guard_result}")
            _finish(role_name, label=label, status="aborted", start_time=_spoke_start)
            return f"Pre-check failed for spoke '{label}': {guard_result}"

    # TeamWork status
    if role_name:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status(role_name, "working")

    if not tools:
        span.end(status="failed", summary="No tools available")
        _finish(role_name, label=label, status="failed", start_time=_spoke_start)
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

    # --- Inject identity + state context into system prompt ---
    identity = build_identity_context(label)
    enhanced_prompt = f"{system_prompt}\n\n## Execution Context\n{identity}"
    if state_context:
        enhanced_prompt += (
            f"\n\n## Current State (observed before execution)\n{state_context}"
        )

    try:
        result = graph.invoke(
            {"messages": [
                SystemMessage(content=enhanced_prompt),
                HumanMessage(content=task),
            ]},
            config={"recursion_limit": recursion_limit},
        )
    except Exception as exc:
        logger.warning("Spoke [%s] failed: %s", label, exc, exc_info=True)
        span.end(status="failed", summary=str(exc)[:200])
        _finish(role_name, label=label, status="failed", start_time=_spoke_start)
        return f"Spoke agent failed: {exc}"

    # Log tool calls for debugging
    tool_count = _log_tool_calls(result, label)

    # Extract the final AI response
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            logger.info("Spoke [%s] completed (%d tool calls): %s", label, tool_count, msg.content[:120])
            span.end(status="completed", summary=msg.content[:200], tool_calls=tool_count)
            _finish(role_name, channel, msg.content, label=label, status="success", start_time=_spoke_start)
            return msg.content

    span.end(status="completed", summary="No output produced", tool_calls=tool_count)
    _finish(role_name, label=label, status="success", start_time=_spoke_start)
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
    *,
    label: str = "",
    status: str = "success",
    start_time: float = 0,
) -> None:
    """Set TeamWork role to idle, post to channel, and record metrics."""
    if role_name:
        from prax.services.teamwork_hooks import set_role_status
        set_role_status(role_name, "idle")
    if channel and content:
        from prax.services.teamwork_hooks import post_to_channel
        post_to_channel(channel, content[:3000], agent_name=role_name or "Agent")

    # Record spoke-level Prometheus metrics.
    if label:
        try:
            import time as _time
            from prax.observability.metrics import SPOKE_CALLS, SPOKE_DURATION
            SPOKE_CALLS.labels(spoke=label, status=status).inc()
            if start_time:
                SPOKE_DURATION.labels(spoke=label).observe(_time.monotonic() - start_time)
        except Exception:
            pass
