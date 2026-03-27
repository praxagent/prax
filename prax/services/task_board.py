"""SQLite-backed task board — Prax's persistent task tracker.

Works independently of TeamWork.  When TeamWork is connected, tasks are
mirrored bidirectionally:
- Local creates/updates push to TeamWork via its API.
- The local DB is the source of truth.

Completed tasks are pruned from the DB after a configurable limit and
archived to ``<workspace>/task_log.jsonl`` for grep/RAG.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_DB_FILENAME = "tasks.db"
_TASK_LOG_FILENAME = "task_log.jsonl"


def _max_completed() -> int:
    from prax.settings import settings
    return settings.task_max_completed

def _task_log_max_bytes() -> int:
    from prax.settings import settings
    return settings.task_log_max_kb * 1024

def _task_log_keep_rotated() -> int:
    from prax.settings import settings
    return settings.task_log_keep_rotated

_db_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    plan_id         TEXT,
    step_number     INTEGER,
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    assigned_to     TEXT DEFAULT 'Executor',
    status          TEXT DEFAULT 'pending',
    priority        TEXT DEFAULT 'normal',
    tw_task_id      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_id);
"""


def _db_path() -> str:
    """Global tasks DB in the project data directory."""
    from prax.settings import settings
    data_dir = settings.workspace_dir or "workspaces"
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, _DB_FILENAME)


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    if path not in _connections:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        _connections[path] = conn
    return _connections[path]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_log_path(user_id: str) -> str:
    from prax.services.workspace_service import workspace_root, ensure_workspace
    ensure_workspace(user_id)
    return os.path.join(workspace_root(user_id), _TASK_LOG_FILENAME)


def _rotate_task_log(path: str) -> None:
    """Rotate the task log when it exceeds the size limit."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < _task_log_max_bytes():
            return

        parent = os.path.dirname(path)
        archive_dir = os.path.join(parent, "archive", "task_logs")
        os.makedirs(archive_dir, exist_ok=True)

        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        archived = os.path.join(archive_dir, f"task_log.{ts}.jsonl")
        shutil.move(path, archived)

        # Prune old archives.
        archives = sorted(
            [f for f in os.listdir(archive_dir) if f.startswith("task_log.")],
            reverse=True,
        )
        for old in archives[_task_log_keep_rotated():]:
            os.remove(os.path.join(archive_dir, old))
    except OSError:
        logger.debug("Task log rotation failed for %s", path, exc_info=True)


def _extract_task_trace(user_id: str, task_id: str) -> list[str]:
    """Extract trace entries correlated with a task ID from the trace log."""
    try:
        from prax.services.workspace_service import workspace_root
        trace_path = os.path.join(workspace_root(user_id), "trace.log")
        if not os.path.isfile(trace_path):
            return []

        with open(trace_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        import re
        # Split into blocks by timestamp header.
        blocks = re.split(r"\n(?==== \d{4}-)", content)
        matched = []
        for block in blocks:
            if f"task={task_id}" in block:
                # Trim to max 2000 chars per block to keep log manageable.
                matched.append(block.strip()[:2000])
        return matched[-20:]  # Last 20 blocks max
    except Exception:
        return []


def _log_task(user_id: str, task: dict) -> None:
    """Append a completed task with its agent trace to the workspace task log (JSONL).

    The log entry includes:
    - Task metadata (id, title, status, timestamps, plan_id)
    - Agent trace excerpts correlated by task_id (tool calls, results, decisions)
    - Outcome (success implied by completion)

    This format is designed for training data extraction and feedback loops.
    """
    try:
        path = _task_log_path(user_id)
        _rotate_task_log(path)

        # Extract correlated trace entries for this task.
        trace_entries = _extract_task_trace(user_id, task["id"])

        entry = {
            **task,
            "archived_at": _now(),
            "trace_excerpt_count": len(trace_entries),
            "trace": trace_entries[:10],  # Cap at 10 excerpts in JSONL
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("Failed to log task to %s", user_id, exc_info=True)


def _sync_to_teamwork_create(task: dict) -> str | None:
    """Push a new task to TeamWork. Returns tw_task_id or None."""
    try:
        from prax.services.teamwork_service import get_teamwork_client
        tw = get_teamwork_client()
        if not tw.enabled or not tw.project_id:
            return None
        tw_id = tw.create_task(
            title=task["title"],
            description=task.get("description", ""),
            assigned_to=task.get("assigned_to"),
            status=task.get("status", "pending"),
        )
        return tw_id
    except Exception:
        logger.debug("TeamWork task create sync failed", exc_info=True)
        return None


def _sync_to_teamwork_update(tw_task_id: str, **kwargs) -> None:
    """Push a task update to TeamWork."""
    try:
        from prax.services.teamwork_service import get_teamwork_client
        tw = get_teamwork_client()
        if not tw.enabled or not tw.project_id or not tw_task_id:
            return
        tw.update_task(tw_task_id, **kwargs)
    except Exception:
        logger.debug("TeamWork task update sync failed", exc_info=True)


# ─── Public API ───────────────────────────────────────────────────────────


def create_task(
    user_id: str,
    title: str,
    description: str = "",
    assigned_to: str = "Executor",
    status: str = "pending",
    priority: str = "normal",
    plan_id: str | None = None,
    step_number: int | None = None,
) -> dict:
    """Create a task and optionally sync to TeamWork."""
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    now = _now()
    task = {
        "id": task_id,
        "user_id": user_id,
        "plan_id": plan_id,
        "step_number": step_number,
        "title": title,
        "description": description,
        "assigned_to": assigned_to,
        "status": status,
        "priority": priority,
        "tw_task_id": None,
        "created_at": now,
        "updated_at": now,
    }

    with _db_lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO tasks
               (id, user_id, plan_id, step_number, title, description,
                assigned_to, status, priority, tw_task_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, user_id, plan_id, step_number, title, description,
             assigned_to, status, priority, None, now, now),
        )
        conn.commit()

    # Sync to TeamWork (outside lock).
    tw_id = _sync_to_teamwork_create(task)
    if tw_id:
        with _db_lock:
            conn = _get_conn()
            conn.execute(
                "UPDATE tasks SET tw_task_id = ? WHERE id = ?",
                (tw_id, task_id),
            )
            conn.commit()
        task["tw_task_id"] = tw_id

    return task


def update_task(task_id: str, **kwargs) -> dict | None:
    """Update a task's fields. Syncs to TeamWork automatically.

    Supported kwargs: status, assigned_to, title, description, priority.
    """
    allowed = {"status", "assigned_to", "title", "description", "priority"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return None

    updates["updated_at"] = _now()

    with _db_lock:
        conn = _get_conn()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        conn.commit()

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    if not row:
        return None

    task = dict(row)

    # Sync relevant fields to TeamWork.
    tw_updates = {k: v for k, v in kwargs.items() if k in {"status", "assigned_to", "title", "description"}}
    if tw_updates and task.get("tw_task_id"):
        _sync_to_teamwork_update(task["tw_task_id"], **tw_updates)

    # If newly completed, prune old completed tasks.
    if kwargs.get("status") == "completed":
        _log_task(task["user_id"], task)
        _prune_completed(task["user_id"])

    return task


def get_task(task_id: str) -> dict | None:
    """Get a single task by ID."""
    with _db_lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def get_tasks(user_id: str, status: str | None = None) -> list[dict]:
    """Get all tasks for a user, optionally filtered by status."""
    with _db_lock:
        conn = _get_conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
                (user_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_plan_tasks(plan_id: str) -> list[dict]:
    """Get all tasks for a specific plan."""
    with _db_lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? ORDER BY step_number",
            (plan_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def find_task_by_step(plan_id: str, step_number: int) -> dict | None:
    """Find a task by plan ID and step number."""
    with _db_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? AND step_number = ?",
            (plan_id, step_number),
        ).fetchone()
    return dict(row) if row else None


def create_plan_task(
    user_id: str,
    plan_id: str,
    goal: str,
    steps: list[dict],
) -> dict:
    """Create ONE board task for a plan, with steps as a checklist description.

    The Kanban board shows plan-level items (not individual steps).
    Steps are tracked as a checklist inside the task description and
    via the YAML plan file for agent_step_done().
    """
    checklist = "\n".join(
        f"- [ ] {s.get('description', f'Step {s.get(\"step\", \"?\")}')})"
        for s in steps
    )
    description = f"**Goal:** {goal}\n\n**Steps:**\n{checklist}"

    task = create_task(
        user_id=user_id,
        title=goal,
        description=description,
        assigned_to="Executor",
        status="in_progress",
        priority="normal",
        plan_id=plan_id,
        step_number=None,  # Plan-level task, not a step
    )
    return task


def update_plan_task_progress(plan_id: str, steps: list[dict]) -> dict | None:
    """Update the plan task's description to reflect step completion progress.

    Called when agent_step_done() marks a step complete — updates the
    checklist in the task description and syncs to TeamWork.
    """
    task = find_task_by_plan(plan_id)
    if not task:
        return None

    # Rebuild checklist from current step state.
    checklist_lines = []
    for s in steps:
        check = "x" if s.get("done") else " "
        checklist_lines.append(f"- [{check}] {s.get('description', f'Step {s.get(\"step\", \"?\")}')})")

    done = sum(1 for s in steps if s.get("done"))
    total = len(steps)
    progress = f"**Progress:** {done}/{total}"

    # Extract goal from existing description or title.
    goal = task.get("title", "")
    description = f"**Goal:** {goal}\n\n**Steps:**\n" + "\n".join(checklist_lines) + f"\n\n{progress}"

    return update_task(task["id"], description=description)


def complete_plan_task(plan_id: str) -> dict | None:
    """Mark the plan-level task as completed."""
    task = find_task_by_plan(plan_id)
    if not task:
        return None
    return update_task(task["id"], status="completed")


def find_task_by_plan(plan_id: str) -> dict | None:
    """Find the board task for a plan (plan-level, not per-step)."""
    with _db_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? AND step_number IS NULL",
            (plan_id,),
        ).fetchone()
    if row:
        return dict(row)
    # Fallback: find any task for this plan.
    with _db_lock:
        row = conn.execute(
            "SELECT * FROM tasks WHERE plan_id = ? ORDER BY created_at LIMIT 1",
            (plan_id,),
        ).fetchone()
    return dict(row) if row else None


def _prune_completed(user_id: str) -> None:
    """Remove old completed tasks beyond the configured limit. Already-logged to JSONL."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT id FROM tasks
                   WHERE user_id = ? AND status = 'completed'
                   ORDER BY updated_at DESC""",
                (user_id,),
            ).fetchall()
            if len(rows) > _max_completed():
                to_delete = [dict(r)["id"] for r in rows[_max_completed():]]
                placeholders = ",".join("?" * len(to_delete))
                conn.execute(
                    f"DELETE FROM tasks WHERE id IN ({placeholders})",
                    to_delete,
                )
                conn.commit()
                logger.debug(
                    "Pruned %d old completed tasks for %s",
                    len(to_delete), user_id,
                )
    except Exception:
        logger.debug("Task pruning failed", exc_info=True)


def _search_jsonl_file(path: str, query_lower: str, results: list[dict], max_results: int) -> None:
    """Search a single JSONL file for matching entries."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in reversed(f.readlines()):
                if len(results) >= max_results:
                    return
                line = line.strip()
                if not line or query_lower not in line.lower():
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass


def search_task_log(user_id: str, query: str, max_results: int = 20) -> list[dict]:
    """Search the task log (current + rotated archives) for completed tasks."""
    results: list[dict] = []
    query_lower = query.lower()

    # Search current file first.
    _search_jsonl_file(_task_log_path(user_id), query_lower, results, max_results)

    # Search rotated archives.
    if len(results) < max_results:
        from prax.services.workspace_service import workspace_root
        archive_dir = os.path.join(workspace_root(user_id), "archive", "task_logs")
        if os.path.isdir(archive_dir):
            for fname in sorted(os.listdir(archive_dir), reverse=True):
                if len(results) >= max_results:
                    break
                if fname.startswith("task_log."):
                    _search_jsonl_file(
                        os.path.join(archive_dir, fname),
                        query_lower, results, max_results,
                    )

    return results
