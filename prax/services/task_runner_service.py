"""Background task runner — auto-executes work assigned to Prax.

Watches two sources every ``task_runner_interval_minutes`` (default 5m)
when ``task_runner_enabled`` is true:

1. **Library Kanban** per space — tasks where ``assignees`` contains
   "prax" in the leftmost non-done column.
2. **Top-level todo list** — entries with ``assignee="prax"`` not yet
   done.

For each pickup we spawn a single synthetic orchestrator turn scoped
to that task. Progress is reported back via Kanban comments (for
Kanban pickups) or by marking the todo complete (for top-level
pickups).  We respect the existing agent_plan/Kanban wall: the
synthetic turn uses agent_plan internally for ephemeral subgoals,
and only the user-created task itself is updated on the Kanban.

Concurrency: one in-flight turn per user at a time. If a turn is
already running when the poll fires, we skip this tick.

State persistence: pause flag lives on disk per-user so restarts
preserve user intent.  Everything else (last run, in-flight) is
in-memory — we don't need to survive crashes for those.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from prax.services import library_service, library_tasks, workspace_service
from prax.settings import settings

logger = logging.getLogger(__name__)

STATE_FILE = "task_runner_state.yaml"
PICKUP_MARKER = "prax"


@dataclass
class _UserState:
    paused: bool = False
    in_flight: bool = False
    last_pick_id: str | None = None
    last_run_ts: float = 0.0


_state: dict[str, _UserState] = {}
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistence (only `paused` survives restarts)
# ---------------------------------------------------------------------------

def _state_path(user_id: str) -> Path:
    return Path(workspace_service.workspace_root(user_id)) / STATE_FILE


def _load_persisted(user_id: str) -> _UserState:
    path = _state_path(user_id)
    if not path.is_file():
        return _UserState()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("task_runner: failed to read state for %s: %s", user_id, e)
        return _UserState()
    return _UserState(paused=bool(data.get("paused", False)))


def _persist_pause(user_id: str, paused: bool) -> None:
    path = _state_path(user_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({"paused": paused}), encoding="utf-8")
    except Exception as e:
        logger.warning("task_runner: failed to persist state for %s: %s", user_id, e)


def _get_state(user_id: str) -> _UserState:
    with _state_lock:
        if user_id not in _state:
            _state[user_id] = _load_persisted(user_id)
        return _state[user_id]


# ---------------------------------------------------------------------------
# Public management API (used by the tasks spoke's management tools)
# ---------------------------------------------------------------------------

def pause(user_id: str) -> None:
    """Stop the runner from picking up new tasks for this user."""
    st = _get_state(user_id)
    with _state_lock:
        st.paused = True
    _persist_pause(user_id, True)


def resume(user_id: str) -> None:
    """Re-enable task pickup for this user."""
    st = _get_state(user_id)
    with _state_lock:
        st.paused = False
    _persist_pause(user_id, False)


def status(user_id: str) -> dict[str, Any]:
    st = _get_state(user_id)
    with _state_lock:
        return {
            "enabled": settings.task_runner_enabled,
            "paused": st.paused,
            "in_flight": st.in_flight,
            "last_pick_id": st.last_pick_id,
            "last_run_ts": st.last_run_ts,
            "interval_minutes": settings.task_runner_interval_minutes,
        }


# ---------------------------------------------------------------------------
# Pickup selection
# ---------------------------------------------------------------------------

def _has_prax_assignee(assignees: list[str] | None) -> bool:
    if not assignees:
        return False
    return any((a or "").strip().lower() == PICKUP_MARKER for a in assignees)


def _pick_kanban_task(user_id: str) -> dict | None:
    """Find one prax-assigned Kanban task in the leftmost non-done column."""
    try:
        spaces = library_service.list_spaces(user_id)
    except Exception as e:
        logger.debug("task_runner: list_spaces failed: %s", e)
        return None
    for space in spaces:
        slug = space.get("slug")
        if not slug:
            continue
        try:
            columns = library_tasks.list_columns(user_id, slug)
        except Exception:
            continue
        # Leftmost non-terminal column.
        target_column = None
        for col in columns:
            col_id = col.get("id", "")
            if col_id and col_id != "done":
                target_column = col_id
                break
        if not target_column:
            continue
        try:
            tasks = library_tasks.list_tasks(user_id, slug, column=target_column)
        except Exception:
            continue
        if isinstance(tasks, dict):
            # Error response shape.
            continue
        for t in tasks:
            if _has_prax_assignee(t.get("assignees")):
                return {
                    "source": "kanban",
                    "space_slug": slug,
                    "task_id": t.get("id"),
                    "title": t.get("title", ""),
                    "description": t.get("description", ""),
                    "column": target_column,
                }
    return None


def _pick_todo_task(user_id: str) -> dict | None:
    """Find one prax-assigned top-level todo that's not done."""
    try:
        todos = workspace_service.list_todos(user_id, show_completed=False)
    except Exception as e:
        logger.debug("task_runner: list_todos failed: %s", e)
        return None
    for t in todos:
        assignee = (t.get("assignee") or "user").strip().lower()
        if assignee == PICKUP_MARKER:
            return {
                "source": "todo",
                "todo_id": t.get("id"),
                "title": t.get("task", ""),
            }
    return None


def _pick_next(user_id: str) -> dict | None:
    """Prefer Kanban (more structured) over top-level todo."""
    return _pick_kanban_task(user_id) or _pick_todo_task(user_id)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _build_synthetic_prompt(pickup: dict) -> str:
    """The prompt given to the synthetic orchestrator turn."""
    source = pickup["source"]
    if source == "kanban":
        return (
            "[TASK_RUNNER_PICKUP — autonomous execution]\n\n"
            f"You have been assigned this task from the user's Library Kanban "
            f"(space: {pickup['space_slug']}, task: {pickup['title']}):\n\n"
            f"TASK TITLE: {pickup['title']}\n"
            f"DESCRIPTION: {pickup.get('description') or '(no description)'}\n\n"
            "Rules:\n"
            "1. The user is NOT present — do NOT ask clarifying questions.\n"
            "2. Take the most reasonable interpretation and execute.\n"
            "3. Use agent_plan for multi-step work — it's your private, "
            "ephemeral working memory. Do NOT mirror its steps onto the "
            "Kanban; the Kanban is the user's board.\n"
            "4. When done, the task runner will comment on the Kanban "
            "with your final response and move the task to done. You "
            "don't need to call library_task_comment or move the task "
            "yourself unless you're posting a mid-progress update.\n"
            "5. Keep your final response concise — it's the completion "
            "note the user will see on the Kanban."
        )
    # Top-level todo
    return (
        "[TASK_RUNNER_PICKUP — autonomous execution]\n\n"
        "You have been assigned this top-level todo:\n\n"
        f"TASK: {pickup['title']}\n\n"
        "Rules:\n"
        "1. The user is NOT present — do NOT ask clarifying questions.\n"
        "2. Take the most reasonable interpretation and execute.\n"
        "3. Use agent_plan for multi-step work.\n"
        "4. When done, the task runner will mark the todo complete. "
        "Keep your final response concise."
    )


def _run_pickup(user_id: str, pickup: dict) -> None:
    """Spawn the synthetic orchestrator turn and handle completion reporting."""
    from prax.agent.orchestrator import ConversationAgent
    from prax.services.conversation_service import ConversationService

    prompt = _build_synthetic_prompt(pickup)
    logger.info(
        "task_runner: picking up %s for %s: %s",
        pickup["source"], user_id, pickup.get("title", "")[:80],
    )

    # Announce start on the Kanban so the user sees pickup immediately.
    if pickup["source"] == "kanban":
        try:
            library_tasks.add_comment(
                user_id, pickup["space_slug"], pickup["task_id"],
                "Prax picked this up via the background task runner.",
                actor="prax",
            )
        except Exception:
            logger.exception("task_runner: failed to post start comment")

    try:
        agent = ConversationAgent(tier="medium")
        svc = ConversationService(agent=agent)
        response = svc.reply(user_id, prompt)
    except Exception as e:
        logger.exception("task_runner: synthetic turn failed for %s", user_id)
        _report_failure(user_id, pickup, str(e))
        return

    _report_success(user_id, pickup, response)


def _report_success(user_id: str, pickup: dict, response: str) -> None:
    response = (response or "").strip() or "(no response)"
    if pickup["source"] == "kanban":
        try:
            library_tasks.add_comment(
                user_id, pickup["space_slug"], pickup["task_id"],
                f"Completed.\n\n{response[:2000]}",
                actor="prax",
            )
            library_tasks.move_task(
                user_id, pickup["space_slug"], pickup["task_id"],
                "done", editor="prax",
            )
        except Exception:
            logger.exception("task_runner: failed to finalise Kanban task")
    elif pickup["source"] == "todo":
        try:
            workspace_service.complete_todo(user_id, [pickup["todo_id"]])
        except Exception:
            logger.exception("task_runner: failed to complete todo")


def _report_failure(user_id: str, pickup: dict, error: str) -> None:
    if pickup["source"] == "kanban":
        try:
            library_tasks.add_comment(
                user_id, pickup["space_slug"], pickup["task_id"],
                f"Task runner failed: {error[:500]}",
                actor="prax",
            )
        except Exception:
            pass
    # Top-level todos: leave the todo in place for user attention.


# ---------------------------------------------------------------------------
# Poll loop (called by APScheduler)
# ---------------------------------------------------------------------------

def _poll_once(user_id: str) -> None:
    """One tick. Called by the scheduler. Safe to invoke directly in tests."""
    if not settings.task_runner_enabled:
        return
    st = _get_state(user_id)
    with _state_lock:
        if st.paused or st.in_flight:
            return
        st.in_flight = True
    try:
        pickup = _pick_next(user_id)
        if pickup is None:
            return
        with _state_lock:
            st.last_pick_id = str(
                pickup.get("task_id") or pickup.get("todo_id") or ""
            )
        _run_pickup(user_id, pickup)
    finally:
        with _state_lock:
            st.in_flight = False
            st.last_run_ts = time.time()


# ---------------------------------------------------------------------------
# Scheduler registration
# ---------------------------------------------------------------------------

def register_user(scheduler, user_id: str) -> None:
    """Add a periodic poll job for this user to the running scheduler."""
    if not settings.task_runner_enabled:
        return
    job_id = f"task_runner:{user_id}"
    # Avoid duplicate registrations if this is re-invoked.
    if scheduler.get_job(job_id):
        return
    scheduler.add_job(
        _poll_once,
        "interval",
        minutes=settings.task_runner_interval_minutes,
        args=[user_id],
        id=job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info(
        "task_runner: registered %s on %d-minute interval",
        user_id, settings.task_runner_interval_minutes,
    )
