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
    # Spokes call tools — low tier is unreliable for tool selection.
    # Individual spokes can still override to "low" via their delegation
    # function if they truly don't need tools (none currently do).
    default_tier: str = "medium",
    role_name: str | None = None,
    channel: str | None = None,
    recursion_limit: int = 80,
    pre_check: Callable[[], str | None] | None = None,
    state_context: str | None = None,
    preserve_tool_result_prefixes: tuple[str, ...] = (),
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
    preserve_tool_result_prefixes:
        Optional exact prefixes for structured tool results that must be
        carried into the delegate result.  Use this when a parent/auditor
        needs source evidence that the spoke LLM might otherwise summarize
        away.
    """
    import time as _time

    from prax.agent.trace import (
        GraphCallbackHandler,
        build_identity_context,
        claim_pending_delegation_context,
        get_current_trace,
        start_span,
    )

    label = config_key.replace("subagent_", "")
    parent_context = None
    if get_current_trace() is None:
        parent_context = claim_pending_delegation_context(
            f"delegate_{label}",
            task=task,
        )
    span = start_span(label, label, parent_context=parent_context)
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

    # TeamWork status + live output
    if role_name:
        from prax.services.teamwork_hooks import push_live_output, set_role_status
        set_role_status(role_name, "working")
        push_live_output(role_name, f"[{label}] Starting: {task[:120]}\n", status="running", append=False)

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

    # --- Inject identity + state context into system prompt ---
    identity = build_identity_context(label)
    enhanced_prompt = f"{system_prompt}\n\n## Execution Context\n{identity}"
    if state_context:
        enhanced_prompt += (
            f"\n\n## Current State (observed before execution)\n{state_context}"
        )

    # Set component context for earned trust and apply autonomy-aware limits.
    from prax.agent.autonomy import get_recursion_limit
    from prax.agent.user_context import bind_tools_user_context, current_component
    current_component.set(label)
    tools = bind_tools_user_context(tools)
    graph = create_react_agent(llm, tools)
    effective_limit = get_recursion_limit(recursion_limit)

    _graph_cb = GraphCallbackHandler(
        parent_span_id=span.span_id,
        graph=span.ctx.graph,
        trace_id=span.trace_id,
        live_agent_name=role_name,
    )
    _cbs: list = [_graph_cb]
    try:
        from prax.observability.callbacks import get_otel_callbacks
        _cbs.extend(get_otel_callbacks())
    except Exception:
        pass

    # Resolve the spoke's context limit from its model
    spoke_model = cfg.get("model") or ""
    spoke_tier = cfg.get("tier") or default_tier

    try:
        # Prepare initial messages
        messages = [
            SystemMessage(content=enhanced_prompt),
            HumanMessage(content=task),
        ]

        # Apply context management — ensures tool results from long
        # spoke runs don't overflow the model's context window.
        try:
            from prax.agent.context_manager import prepare_context
            messages, ctx_budget = prepare_context(
                messages, tier=spoke_tier, model=spoke_model,
            )
            logger.debug(
                "Spoke [%s] context: %d/%d tokens",
                label, ctx_budget.total, ctx_budget.limit,
            )
        except Exception:
            pass  # Context management is best-effort for spokes

        # Invoke with context overflow recovery (compact and retry up to 3 times)
        _ctx_retries = 0
        while True:
            try:
                result = graph.invoke(
                    {"messages": messages},
                    config={"recursion_limit": effective_limit, "callbacks": _cbs},
                )
                break
            except Exception as invoke_exc:
                from langchain_core.exceptions import ContextOverflowError
                if isinstance(invoke_exc, ContextOverflowError) and _ctx_retries < 3:
                    _ctx_retries += 1
                    logger.warning(
                        "Spoke [%s] context overflow (attempt %d/3) — compacting",
                        label, _ctx_retries,
                    )
                    try:
                        from prax.services.health_telemetry import EventCategory, Severity, record_event
                        record_event(
                            EventCategory.CONTEXT_OVERFLOW, Severity.WARNING,
                            component=f"spoke:{label}",
                            details=f"Attempt {_ctx_retries}/3",
                        )
                    except Exception:
                        pass
                    try:
                        from prax.agent.context_manager import (
                            clear_old_tool_results,
                            compact_history,
                            get_context_limit,
                            truncate_history,
                        )
                        shrunk = int(get_context_limit(spoke_tier, spoke_model) * (0.8 ** _ctx_retries))
                        messages = clear_old_tool_results(messages, keep_last_n=3)
                        messages = compact_history(messages, shrunk, tier=spoke_tier)
                        messages = truncate_history(messages, shrunk)
                        continue
                    except Exception:
                        pass
                raise  # re-raise if not context overflow or retries exhausted

    except Exception as exc:
        logger.warning("Spoke [%s] failed: %s", label, exc, exc_info=True)
        span.end(status="failed", summary=str(exc)[:200])
        _finish(role_name, label=label, status="failed", start_time=_spoke_start)
        try:
            from prax.services.health_telemetry import EventCategory, Severity, record_event
            record_event(
                EventCategory.SPOKE_FAILURE, Severity.WARNING,
                component=label,
                details=f"{type(exc).__name__}: {str(exc)[:200]}",
                latency_ms=((_time.monotonic() - _spoke_start) * 1000),
            )
        except Exception:
            pass
        return f"Spoke agent failed: {exc}"

    # Log tool calls for debugging
    tool_count = _log_tool_calls(result, label, role_name=role_name)

    # Extract the final AI response
    for msg in reversed(result.get("messages", [])):
        if isinstance(msg, AIMessage) and msg.content:
            final_content = _append_preserved_tool_results(
                str(msg.content),
                result.get("messages", []),
                preserve_tool_result_prefixes,
            )
            logger.info("Spoke [%s] completed (%d tool calls): %s", label, tool_count, final_content[:120])
            span.end(status="completed", summary=final_content[:200], tool_calls=tool_count)
            _finish(role_name, channel, final_content, label=label, status="success", start_time=_spoke_start)
            try:
                from prax.services.health_telemetry import EventCategory, record_event
                record_event(
                    EventCategory.SPOKE_SUCCESS,
                    component=label,
                    details=f"{tool_count} tool calls",
                    latency_ms=((_time.monotonic() - _spoke_start) * 1000),
                )
            except Exception:
                pass
            return final_content

    span.end(status="completed", summary="No output produced", tool_calls=tool_count)
    _finish(role_name, label=label, status="success", start_time=_spoke_start)
    return f"Spoke [{label}] completed but produced no output."


def _append_preserved_tool_results(
    response: str,
    messages: list,
    prefixes: tuple[str, ...] = (),
) -> str:
    """Append selected structured tool outputs to a spoke's final response.

    Parent agents only receive the spoke's final ToolMessage, not every
    nested tool result.  For evidence-bearing tools, a terse LLM summary can
    accidentally hide the verification block from parent-level auditors.
    """
    normalized_prefixes = tuple(prefix.strip().upper() for prefix in prefixes if prefix.strip())
    if not normalized_prefixes:
        return response

    preserved: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = str(msg.content or "").strip()
        if not content:
            continue
        upper_content = content.lstrip().upper()
        if not any(upper_content.startswith(prefix) for prefix in normalized_prefixes):
            continue
        if content in response or content in seen:
            continue
        seen.add(content)
        preserved.append(content)

    if not preserved:
        return response

    evidence = "\n\n".join(preserved)
    return f"{response.rstrip()}\n\n[Tool evidence preserved for audit]\n{evidence}"


def _log_tool_calls(result: dict, label: str, role_name: str | None = None) -> int:
    """Log all tool calls and flag errors.  Returns the total call count."""
    tool_count = 0
    live_lines: list[str] = []
    for msg in result.get("messages", []):
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_count += 1
                tool_line = f"  → {tc.get('name')}({str(tc.get('args', {}))[:80]})"
                logger.info("Spoke [%s] tool: %s(%s)", label, tc.get("name"), str(tc.get("args", {}))[:80])
                live_lines.append(tool_line)
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:200]
            if "error" in preview.lower() or "fail" in preview.lower():
                logger.warning("Spoke [%s] tool error [%s]: %s", label, msg.name, preview)
                live_lines.append(f"  ✗ {msg.name}: {preview[:120]}")
            else:
                live_lines.append(f"  ✓ {msg.name}: {preview[:120]}")

    # Push tool call log to TeamWork live output
    if role_name and live_lines:
        from prax.services.teamwork_hooks import push_live_output
        push_live_output(role_name, "\n".join(live_lines) + "\n")

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
    """Set TeamWork role to idle, post to channel, push final live output, and record metrics."""
    if role_name:
        from prax.services.teamwork_hooks import push_live_output, set_role_status
        set_role_status(role_name, "idle")
        live_status = "completed" if status == "success" else status
        summary = f"\n[{label}] {live_status}"
        if content:
            summary += f": {content[:200]}"
        push_live_output(role_name, summary + "\n", status=live_status)
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
