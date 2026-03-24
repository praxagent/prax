"""Lightweight safety/governance layer for side-effectful agent actions.

Classifies tools by risk level and provides helpers for confirmation
gating and structured audit logging.  Standalone — no prax imports.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum


class RiskLevel(Enum):
    """How dangerous a tool invocation is.

    LOW    – read-only or local workspace writes (git-backed, reversible)
    MEDIUM – external reads (HTTP GET, API queries), local state changes
    HIGH   – external writes (messages, POST/PUT/DELETE), sandbox exec, file send
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── tool classification ──────────────────────────────────────────────

_HIGH: set[str] = {
    # browser state changes / form submission
    "browser_click",
    "browser_fill",
    "browser_request_login",
    "browser_finish_login",
    # outbound file send
    "workspace_send_file",
    # automated message scheduling
    "schedule_create",
    "schedule_update",
    "schedule_reminder",
    # arbitrary code execution
    "sandbox_execute",
    "sandbox_start",
    "sandbox_message",
    # system mutation
    "plugin_activate",
    "plugin_write",
    "self_improve_deploy",
}

_MEDIUM: set[str] = {
    # external reads
    "browser_open",
    "browser_read_page",
    "browser_screenshot",
    "browser_find",
    "fetch_url_content",
    "background_search_tool",
    "arxiv_search",
    "arxiv_fetch_papers",
    "rss_check",
    # local state changes
    "schedule_delete",
    "schedule_reload",
    "reminder_delete",
    # sandbox lifecycle
    "sandbox_review",
    "sandbox_finish",
    "sandbox_abort",
    # publishable content
    "note_create",
    "note_update",
    "url_to_note",
    "pdf_to_note",
    "arxiv_to_note",
    "course_create",
    "course_update",
    "course_publish",
    # workspace writes
    "project_create",
    "project_add_note",
    "project_add_link",
    "project_add_source",
}

TOOL_RISK_MAP: dict[str, RiskLevel] = {
    name: RiskLevel.HIGH for name in _HIGH
} | {name: RiskLevel.MEDIUM for name in _MEDIUM}


# ── public helpers ───────────────────────────────────────────────────


def get_risk_level(tool_name: str) -> RiskLevel:
    """Return the risk level for *tool_name*, defaulting to MEDIUM."""
    return TOOL_RISK_MAP.get(tool_name, RiskLevel.MEDIUM)


def requires_confirmation(tool_name: str) -> bool:
    """Return True when the tool should be gated behind user confirmation."""
    return get_risk_level(tool_name) is RiskLevel.HIGH


_TRUNCATE = 200


def _truncate(text: str | None, limit: int = _TRUNCATE) -> str | None:
    if text is None:
        return None
    return text[:limit] + "..." if len(text) > limit else text


def log_action(
    tool_name: str,
    risk: RiskLevel,
    args: dict,
    result: str | None = None,
) -> dict:
    """Build a structured audit-log entry (does not persist anywhere)."""
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "tool_name": tool_name,
        "risk": risk.value,
        "args": _truncate(str(args)),
        "result": _truncate(result),
    }


# ── decorator ────────────────────────────────────────────────────────


def risk_tool(*, risk: RiskLevel):
    """Decorator that wraps LangChain's ``@tool`` and attaches risk metadata.

    Usage::

        @risk_tool(risk=RiskLevel.HIGH)
        def sandbox_execute(code: str) -> str:
            \"\"\"Execute code in sandbox.\"\"\"
            ...

    The resulting object is a standard LangChain ``StructuredTool`` with an
    extra ``_risk_level`` attribute that ``wrap_with_governance`` reads.
    """
    from langchain_core.tools import tool as _lc_tool

    def decorator(func):
        lc_tool = _lc_tool(func)
        lc_tool._risk_level = risk
        return lc_tool

    return decorator
