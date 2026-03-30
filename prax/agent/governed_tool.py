"""Single interception point for tool governance.

Every tool invocation passes through ``wrap_with_governance`` before
reaching the LangGraph agent.  This is the ONE choke point where:

1. The tool's risk level is classified (with earned-trust downgrade)
2. Arguments are summarized/scrubbed
3. Confirmation requirement is evaluated (with smart auto-approve)
4. An audit record is emitted to the workspace trace log
5. Tool call budget is tracked (with agent-initiated escalation)

Wired into the agent via ``tool_registry.get_registered_tools()``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from prax.agent.action_policy import (
    RiskLevel,
    SourceReliability,
    get_risk_level,
    get_tool_capability,
    log_action,
)

logger = logging.getLogger(__name__)

# Module-level audit log buffer.  Entries are flushed to the workspace
# trace by the orchestrator after each turn (via ``drain_audit_log``).
_audit_buffer: list[dict] = []

# Tracks which HIGH-risk tools have been called this turn.  First call
# returns a confirmation prompt; second call (same tool name OR any
# previously-seen tool) executes.  Once the user confirms ONE high-risk
# action, all high-risk tools are unlocked for the remainder of the turn.
# Cleared on ``drain_audit_log()`` (i.e. once per agent turn).
_high_risk_seen: set[str] = set()
_high_risk_confirmed: bool = False

# Budget tracking — soft limit on tool calls per turn.
_tool_call_count: int = 0
_tool_call_budget: int = 0  # Set from settings at turn start

# Pattern for detecting user-requested actions in browser context.
_USER_ACTION_PATTERN = re.compile(
    r"\b(click|press|tap|fill|submit|log\s*in|sign\s*in|enter|type|"
    r"open|navigate|go to|visit|browse|check|read|scroll|search)\b",
    re.IGNORECASE,
)


def drain_audit_log() -> list[dict]:
    """Return and clear all buffered audit entries since the last drain."""
    global _high_risk_confirmed, _tool_call_count, _tool_call_budget
    entries = list(_audit_buffer)
    _audit_buffer.clear()
    _high_risk_seen.clear()
    _high_risk_confirmed = False
    _tool_call_count = 0
    _tool_call_budget = 0
    return entries


def init_turn_budget(budget: int) -> None:
    """Set the soft tool-call budget for the current turn."""
    global _tool_call_budget, _tool_call_count
    _tool_call_budget = budget
    _tool_call_count = 0


def extend_budget(additional: int) -> None:
    """Increase the tool-call budget (called by request_extended_budget)."""
    global _tool_call_budget
    _tool_call_budget += min(additional, 50)  # cap single extension at 50


def get_budget_status() -> tuple[int, int]:
    """Return (calls_used, budget)."""
    return _tool_call_count, _tool_call_budget


def _user_explicitly_requested_action(tool_name: str) -> bool:
    """Check if the user's message explicitly requested this tool's action.

    When the user says "click the login button" and the agent calls
    browser_click, we should auto-approve rather than blocking with a
    confirmation prompt.
    """
    from prax.agent.user_context import current_user_message
    msg = current_user_message.get("")
    if not msg:
        return False

    # Only auto-approve browser interaction tools
    if tool_name not in (
        "browser_click", "browser_fill", "browser_request_login",
        "browser_finish_login",
    ):
        return False

    return bool(_USER_ACTION_PATTERN.search(msg))


def wrap_with_governance(tool: BaseTool) -> BaseTool:
    """Wrap a tool with governance: risk classification, audit logging,
    and (for HIGH-risk tools) a confirmation gate.

    Returns a ``StructuredTool`` that delegates to the original tool
    through the governance layer.
    """
    tool_name = tool.name
    static_risk = getattr(tool, "_risk_level", None) or get_risk_level(tool_name)

    # Resolve capability metadata for epistemic tagging.
    capability = get_tool_capability(tool_name)
    reliability = (
        capability.get("reliability", SourceReliability.INFORMATIONAL)
        if capability
        else None
    )
    epistemic_note = capability.get("epistemic_note", "") if capability else ""

    def _governed_run(**kwargs: Any) -> Any:
        global _high_risk_confirmed, _tool_call_count

        # --- Earned trust: dynamic risk adjustment ---
        risk = static_risk
        if risk is RiskLevel.HIGH:
            try:
                from prax.agent.earned_trust import get_trust_adjustments
                from prax.agent.user_context import current_component
                component = current_component.get("orchestrator")
                trust = get_trust_adjustments(component)
                if tool_name in trust.risk_downgrade_eligible:
                    risk = RiskLevel.MEDIUM
                    logger.debug(
                        "Earned trust: downgraded %s from HIGH to MEDIUM for %s",
                        tool_name, component,
                    )
            except Exception:
                pass

        # --- Budget tracking ---
        if _tool_call_budget > 0:
            _tool_call_count += 1
            if _tool_call_count > _tool_call_budget and tool_name != "request_extended_budget":
                _audit_buffer.append(log_action(
                    tool_name, risk, kwargs,
                    result="BLOCKED — tool call budget exhausted",
                ))
                return (
                    f"Tool call budget exhausted ({_tool_call_budget} calls used). "
                    f"Use request_extended_budget(reason, additional_calls) to "
                    f"request more calls if the task genuinely requires it."
                )

        # --- HIGH-risk gate with smart auto-approve ---
        if risk is RiskLevel.HIGH and not _high_risk_confirmed:
            # Smart confirmation: if the user explicitly requested this
            # action (e.g., "click the login button"), auto-approve.
            if _user_explicitly_requested_action(tool_name):
                _high_risk_confirmed = True
                logger.info(
                    "Smart auto-approve: %s (user explicitly requested action)",
                    tool_name,
                )
            elif tool_name not in _high_risk_seen:
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
            else:
                # User confirmed — unlock all HIGH-risk tools for this turn.
                _high_risk_confirmed = True

        # Execute the tool.
        logger.info("Tool %s starting [%s] (args=%s)", tool_name, risk.value, _summarize_args(kwargs))
        from prax.services.teamwork_hooks import set_role_status
        set_role_status("Executor", "working")
        if risk is RiskLevel.HIGH:
            set_role_status("Auditor", "working")
        try:
            result = tool.invoke(kwargs if kwargs else {})
            result_str = str(result) if result is not None else None
            _audit_buffer.append(log_action(tool_name, risk, kwargs, result=result_str))
            logger.info("Tool %s finished [%s]", tool_name, risk.value)

            # Epistemic tagging: prepend source-reliability metadata so the
            # LLM knows how much to trust this result for factual claims.
            if reliability is not None and result is not None:
                result = _tag_result(result, reliability, epistemic_note)

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


_RELIABILITY_TAGS: dict[SourceReliability, str] = {
    SourceReliability.INFORMATIONAL: (
        "[INFORMATIONAL SOURCE — general web content, not structured data. "
        "Do NOT state specific numbers, prices, statistics, rankings, or "
        "quantities from this result as verified facts. "
        "Use only for background context and general understanding.]"
    ),
    SourceReliability.INDICATIVE: (
        "[INDICATIVE SOURCE — data may be approximate or stale. "
        "If citing specific values, label them as approximate and name the source URL.]"
    ),
    SourceReliability.VERIFIED: (
        "[VERIFIED SOURCE — structured data from a purpose-built API. "
        "Values can be cited directly with source attribution.]"
    ),
}


def _tag_result(
    result: Any,
    reliability: SourceReliability,
    epistemic_note: str = "",
) -> Any:
    """Prepend epistemic metadata to a tool result string.

    Only tags string results; non-string results pass through unchanged.
    """
    if not isinstance(result, str):
        return result
    tag = _RELIABILITY_TAGS.get(reliability, "")
    if epistemic_note:
        tag = f"{tag}\n{epistemic_note}" if tag else epistemic_note
    if tag:
        return f"{tag}\n\n{result}"
    return result


def _summarize_args(args: dict, max_len: int = 120) -> str:
    """Compact string summary of tool args for logging."""
    s = str(args)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
