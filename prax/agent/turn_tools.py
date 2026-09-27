"""Let the agent stop the user's other running tasks when asked to.

A bare "stop" is handled before the model runs (conversation_service). This
covers the rest — "stop solving that", "cancel the leetcode thing" — where the
model, told what is running (see conversation_service's running-task note),
decides whether that is what the person means.
"""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id


@tool
def stop_running_task() -> str:
    """Stop the user's OTHER tasks that are still running from earlier messages.

    Use only when the user asks to stop, cancel or abandon work that is still
    in progress (you are told in context when something is running). It does
    not undo what those tasks already did, and it never stops this reply.
    """
    from prax.agent.governed_tool import current_turn_state
    from prax.services import turn_registry

    uid = current_user_id.get() or ""
    stopped = turn_registry.cancel(uid, exclude=current_turn_state().turn)
    if not stopped:
        return "Nothing else is running for this user."
    return "Stopped: " + "; ".join(turn_registry.describe(t) for t in stopped)


def build_turn_tools() -> list:
    from prax.settings import settings
    return [stop_running_task] if getattr(settings, "turn_stop_enabled", False) else []
