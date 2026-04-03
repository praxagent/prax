"""Canonical trace event type vocabulary.

All trace emitters and consumers share this definition so that filtering,
logging, and documentation stay in sync.  Standalone — no prax imports.

Usage::

    from prax.trace_events import TraceEvent

    entry = {"type": TraceEvent.TOOL_CALL, "content": "..."}
    # or filter:
    search_trace(uid, "sandbox", type_filter=TraceEvent.AUDIT)
"""
from __future__ import annotations

from enum import StrEnum


class TraceEvent(StrEnum):
    """Known trace entry types.

    Inherits from ``str`` so values can be used directly as dict values
    and compared with plain strings (e.g. ``entry["type"] == TraceEvent.AUDIT``).
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AUDIT = "audit"
    ERROR = "error"
    PLUGIN_IMPORT = "plugin_import"
    PLUGIN_ACTIVATE = "plugin_activate"
    PLUGIN_BLOCK = "plugin_block"
    PLUGIN_ROLLBACK = "plugin_rollback"
    PLUGIN_REMOVE = "plugin_remove"
    PLUGIN_SECURITY_WARN = "plugin_security_warn"
    TIER_CHOICE = "tier_choice"
    THINK = "think"
    PREDICTION_ERROR = "prediction_error"
    EPISTEMIC_GATE = "epistemic_gate"
    LOGPROB_ENTROPY = "logprob_entropy"
    SEMANTIC_ENTROPY = "semantic_entropy"
    FEEDBACK = "feedback"
    FAILURE_CASE = "failure_case"
    EVAL_RESULT = "eval_result"

    @classmethod
    def values(cls) -> set[str]:
        """Return all known event type strings."""
        return {e.value for e in cls}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Check whether *value* is a known event type."""
        return value in cls.values()
