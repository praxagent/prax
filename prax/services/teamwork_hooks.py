"""Fire-and-forget TeamWork hooks for role agent status and channel routing.

Every function here is a no-op when TeamWork is disabled or not connected.
Exceptions are caught and logged at DEBUG level — these hooks must never
break the core agent loop.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _tw():
    """Return the TeamWork client, or None if unavailable."""
    try:
        from prax.services.teamwork_service import get_teamwork_client
        tw = get_teamwork_client()
        if tw.enabled and tw.project_id:
            return tw
    except Exception:
        pass
    return None


def set_role_status(role_name: str, status: str) -> None:
    """Set a role agent's status (idle/working/offline)."""
    try:
        tw = _tw()
        if tw:
            tw.set_agent_status(role_name, status)
    except Exception:
        logger.debug("TeamWork hook: set_role_status(%s, %s) failed", role_name, status, exc_info=True)


def reset_all_idle() -> None:
    """Set all role agents to idle. Call at end of each agent turn."""
    for role in ("Planner", "Researcher", "Executor", "Skeptic", "Auditor"):
        set_role_status(role, "idle")


def post_to_channel(channel: str, content: str, agent_name: str | None = None) -> None:
    """Post a message to a TeamWork channel.

    If the current request originated from a DM, ALL messages are redirected
    to that DM channel so the user sees the full conversation in one place.
    """
    try:
        tw = _tw()
        if tw:
            channel_id = None
            from prax.agent.user_context import current_channel_id
            ctx_channel = current_channel_id.get(None)
            if ctx_channel:
                channel_id = ctx_channel
            tw.send_message(content=content, channel=channel, agent_name=agent_name, channel_id=channel_id)
    except Exception:
        logger.debug("TeamWork hook: post_to_channel(%s) failed", channel, exc_info=True)


def mirror_plan_to_tasks(goal: str, steps: list[dict]) -> list[str]:
    """Create TeamWork tasks mirroring an agent plan. Returns task IDs."""
    task_ids: list[str] = []
    try:
        tw = _tw()
        if not tw:
            return task_ids
        for s in steps:
            tid = tw.create_task(
                title=s.get("description", f"Step {s.get('step', '?')}"),
                assigned_to="Executor",
                status="pending",
            )
            if tid:
                task_ids.append(tid)
    except Exception:
        logger.debug("TeamWork hook: mirror_plan_to_tasks failed", exc_info=True)
    return task_ids
