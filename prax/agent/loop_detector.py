"""Tool call loop detection.

Detects when the agent is stuck calling the same tool with the same
arguments repeatedly.  Integrates into the governed_tool layer.

Escalation ladder:
  3 repeats → reflection prompt (soft nudge to try a different approach)
  5 repeats → stronger warning with alternative suggestions
  7 repeats → hard block, force the agent to stop and explain

The detector is per-turn — cleared when ``drain_audit_log()`` resets
governance state at the end of each turn.
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REFLECT_THRESHOLD = 3   # Soft nudge: "you've called this N times"
WARN_THRESHOLD = 5      # Stronger: "try a different tool or approach"
BLOCK_THRESHOLD = 7     # Hard block: "stop repeating, explain why"

# Tools that are expected to be called repeatedly (polling, pagination).
_EXEMPT_TOOLS = frozenset({
    "request_extended_budget",
    "plan_create",
    "plan_mark_step_done",
    "stm_write",
    "stm_read",
})

# ---------------------------------------------------------------------------
# Per-turn state
# ---------------------------------------------------------------------------

# Maps a call signature (hash of tool_name + args) to its count this turn.
_call_counts: dict[str, int] = {}
# Maps signature to the tool name for readable messages.
_call_names: dict[str, str] = {}


def reset() -> None:
    """Clear loop detection state (called at turn boundaries)."""
    _call_counts.clear()
    _call_names.clear()


def _signature(tool_name: str, kwargs: dict) -> str:
    """Create a stable hash from tool name + sorted arguments."""
    # Sort args for stability, truncate large values to avoid
    # hash instability from minor output differences.
    parts = [tool_name]
    for key in sorted(kwargs):
        val = str(kwargs[key])[:200]
        parts.append(f"{key}={val}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def check(tool_name: str, kwargs: dict) -> str | None:
    """Check if this tool call is a repeated loop.

    Returns None if OK, or a message string to return to the agent
    instead of executing the tool.
    """
    if tool_name in _EXEMPT_TOOLS:
        return None

    sig = _signature(tool_name, kwargs)
    _call_counts[sig] = _call_counts.get(sig, 0) + 1
    _call_names[sig] = tool_name
    count = _call_counts[sig]

    if count >= BLOCK_THRESHOLD:
        logger.warning(
            "Loop blocked: %s called %d times with same args", tool_name, count,
        )
        try:
            from prax.services.health_telemetry import EventCategory, Severity, record_event
            record_event(
                EventCategory.TOOL_ERROR, Severity.WARNING,
                component="loop_detector",
                details=f"Blocked {tool_name} after {count} identical calls",
            )
        except Exception:
            pass
        return (
            f"LOOP DETECTED: You have called {tool_name} with the same arguments "
            f"{count} times this turn. This is almost certainly a bug in your "
            f"reasoning. STOP calling this tool. Instead:\n"
            f"1. Explain to the user what you were trying to do\n"
            f"2. Explain why it's not working\n"
            f"3. Ask for guidance or try a completely different approach"
        )

    if count >= WARN_THRESHOLD:
        logger.warning(
            "Loop warning: %s called %d times with same args", tool_name, count,
        )
        return (
            f"WARNING: You have called {tool_name} with identical arguments "
            f"{count} times. You appear to be stuck in a loop. "
            f"Try a DIFFERENT tool or DIFFERENT arguments. "
            f"If the tool keeps failing, tell the user what's happening "
            f"instead of retrying."
        )

    if count >= REFLECT_THRESHOLD:
        logger.info(
            "Loop reflection: %s called %d times with same args", tool_name, count,
        )
        return (
            f"Note: You've called {tool_name} with the same arguments "
            f"{count} times. Before calling it again, consider: "
            f"Is the result going to be different this time? "
            f"If not, try a different approach or different arguments."
        )

    return None


def get_loop_stats() -> dict:
    """Return current loop detection state for debugging."""
    repeated = {
        _call_names.get(sig, "unknown"): count
        for sig, count in _call_counts.items()
        if count >= REFLECT_THRESHOLD
    }
    return {
        "unique_signatures": len(_call_counts),
        "repeated_tools": repeated,
        "total_calls": sum(_call_counts.values()),
    }
