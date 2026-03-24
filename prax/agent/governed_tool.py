"""Single interception point for tool governance.

Every tool invocation passes through ``wrap_with_governance`` before
reaching the LangGraph agent.  This is the ONE choke point where:

1. The tool's risk level is classified
2. Arguments are summarized/scrubbed
3. Confirmation requirement is evaluated
4. An audit record is emitted to the workspace trace log

Wired into the agent via ``tool_registry.get_registered_tools()``.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from prax.agent.action_policy import (
    RiskLevel,
    get_risk_level,
    log_action,
)

logger = logging.getLogger(__name__)

# Module-level audit log buffer.  Entries are flushed to the workspace
# trace by the orchestrator after each turn (via ``drain_audit_log``).
_audit_buffer: list[dict] = []

# Tracks which HIGH-risk tools have been called this turn.  First call
# returns a confirmation prompt; second call (same tool) executes.
# Cleared on ``drain_audit_log()`` (i.e. once per agent turn).
_high_risk_seen: set[str] = set()


def drain_audit_log() -> list[dict]:
    """Return and clear all buffered audit entries since the last drain."""
    entries = list(_audit_buffer)
    _audit_buffer.clear()
    _high_risk_seen.clear()
    return entries


def wrap_with_governance(tool: BaseTool) -> BaseTool:
    """Wrap a tool with governance: risk classification, audit logging,
    and (for HIGH-risk tools) a confirmation gate.

    Returns a ``StructuredTool`` that delegates to the original tool
    through the governance layer.
    """
    tool_name = tool.name
    risk = getattr(tool, "_risk_level", None) or get_risk_level(tool_name)

    def _governed_run(**kwargs: Any) -> Any:
        # HIGH-risk gate: first invocation returns a warning, second executes.
        if risk is RiskLevel.HIGH and tool_name not in _high_risk_seen:
            _high_risk_seen.add(tool_name)
            _audit_buffer.append(log_action(
                tool_name, risk, kwargs, result="BLOCKED — awaiting confirmation",
            ))
            logger.info(
                "HIGH-risk tool %s blocked pending confirmation (args=%s)",
                tool_name, _summarize_args(kwargs),
            )
            return (
                f"⚠️ This action ({tool_name}) is classified as HIGH risk. "
                f"Please confirm with the user before proceeding. "
                f"To execute, call {tool_name} again with the same arguments."
            )

        # Execute the tool.
        try:
            result = tool.invoke(kwargs if kwargs else {})
            result_str = str(result) if result is not None else None
            _audit_buffer.append(log_action(tool_name, risk, kwargs, result=result_str))
            if risk is not RiskLevel.LOW:
                logger.info(
                    "Tool %s executed [%s] (args=%s)",
                    tool_name, risk.value, _summarize_args(kwargs),
                )
            return result
        except Exception as exc:
            _audit_buffer.append(log_action(
                tool_name, risk, kwargs, result=f"ERROR: {exc}",
            ))
            raise

    return StructuredTool.from_function(
        func=_governed_run,
        name=tool_name,
        description=tool.description,
        args_schema=tool.args_schema,
    )


def _summarize_args(args: dict, max_len: int = 120) -> str:
    """Compact string summary of tool args for logging."""
    s = str(args)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
