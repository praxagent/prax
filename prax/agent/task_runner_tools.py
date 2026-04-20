"""LangChain tool wrappers for the background task runner."""
from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


@tool
def task_runner_status() -> str:
    """Report whether the background task runner is enabled, paused, or
    currently working on a task. Call this when the user asks "is the
    task runner on?" or "what's Prax doing in the background?"."""
    from prax.services import task_runner_service
    st = task_runner_service.status(_get_user_id())
    enabled = st["enabled"]
    if not enabled:
        return (
            "Task runner is **disabled** in this deployment. Set "
            "TASK_RUNNER_ENABLED=true to enable it. When enabled, "
            "Prax polls the user's Kanban and top-level todo list "
            "every few minutes and picks up tasks assigned to 'prax'."
        )
    lines = [
        f"Task runner: **enabled**, polling every {st['interval_minutes']}m",
        f"  Paused: {st['paused']}",
        f"  In-flight: {st['in_flight']}",
    ]
    if st["last_pick_id"]:
        lines.append(f"  Last pickup id: {st['last_pick_id']}")
    if st["last_run_ts"]:
        ts = datetime.fromtimestamp(st["last_run_ts"], tz=UTC).isoformat()
        lines.append(f"  Last tick: {ts}")
    return "\n".join(lines)


@tool
def task_runner_pause() -> str:
    """Pause the background task runner for the current user. No new
    tasks will be picked up until task_runner_resume is called."""
    from prax.services import task_runner_service
    task_runner_service.pause(_get_user_id())
    return "Task runner paused. No new tasks will be picked up until you resume."


@tool
def task_runner_resume() -> str:
    """Resume the background task runner after a pause."""
    from prax.services import task_runner_service
    task_runner_service.resume(_get_user_id())
    return "Task runner resumed. Pickup will continue on the next poll."
