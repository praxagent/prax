"""Process-scoped orchestrator tier boost for ``self_upgrade_tier``.

``self_upgrade_tier`` is the agent's deliberate "I need more capability" lever.
Unlike ``llm_config_write`` (which is meant to persist a routing change), a
tier boost should be *transient*: it raises the orchestrator's base tier for
the rest of the running process but is **not** written to config, so a restart
returns to the shipped base tier. This prevents a single hard task from
permanently — and expensively — pinning the orchestrator to a high tier
(the drift that ``llm_routing.yaml`` used to accumulate).

It complements the reactive, turn-local auto-escalation
(``AUTO_TIER_ESCALATION``): auto-escalation bumps a tier *within* a thrashing
turn and resets next turn; a self-upgrade raises the *floor* the base resets
to, for the rest of the session.

State is a plain module global (process-scoped, reset on restart) rather than a
ContextVar so it survives across turns and can't be lost to a thread boundary.
"""
from __future__ import annotations

_floor: str | None = None


def set_session_tier_floor(tier: str) -> None:
    """Raise the session-wide orchestrator tier floor (in memory only)."""
    global _floor
    _floor = tier


def get_session_tier_floor() -> str | None:
    """Return the current session tier floor, or None if unset."""
    return _floor


def clear_session_tier_floor() -> None:
    """Forget any session boost (returns the orchestrator to its base tier)."""
    global _floor
    _floor = None
