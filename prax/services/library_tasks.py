"""Kanban-style task tracker for Library projects.

Each project gets a ``.tasks.yaml`` file storing:

- **columns**: ordered list of Kanban columns (``todo``, ``doing``, ``done``
  by default; humans can add/rename/delete)
- **tasks**: list of task entries with title, description, column, assignees,
  due date, author, optional reminder_id, activity log, and comments

Design principles
-----------------

- **Both human and Prax can freely create, move, and edit any task** — no
  ``prax_may_edit`` gate like notes have.  Tasks are inherently
  collaborative.  The ``author`` field captures who created it and the
  ``activity`` log captures who did what next.
- **Activity log is append-only.**  Every create / move / update / comment
  / delete adds an entry via ``_append_activity``.
- **Reminders integrate with ``scheduler_service``.**  A task with a
  ``due_date`` and ``reminder_enabled=True`` auto-creates a one-time
  reminder that fires over the project's configured channel.  The
  resulting ``reminder_id`` is stored on the task so we can cancel the
  reminder when the task is completed, deleted, or rescheduled.
- **Separate from Prax's internal TaskCreate/TaskList system.**  That one
  is for Prax's own work orchestration; this one is for human-visible
  project management.  They don't share state.

Storage
-------

``library/projects/{project}/.tasks.yaml``::

    columns:
      - id: todo
        name: "To Do"
      - id: doing
        name: "Doing"
      - id: done
        name: "Done"
    tasks:
      - id: tsk-abc123
        title: "Write spec"
        description: "..."
        column: doing
        author: human          # "human" or "prax"
        assignees: ["prax"]    # freeform strings
        due_date: "2026-04-15T17:00:00-07:00"
        reminder_enabled: true
        reminder_id: "rem-..."  # scheduler reminder tracking
        reminder_channel: "all" # overrides project default
        checklist:
          - text: "Endpoints"
            done: true
          - text: "Auth"
            done: false
        activity:
          - actor: human
            at: "2026-04-08T10:00:00+00:00"
            action: created
          - actor: prax
            at: "2026-04-08T10:15:00+00:00"
            action: moved
            from: todo
            to: doing
        comments: []
        created_at: ...
        updated_at: ...
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from prax.services.library_service import (
    SPACES_DIR,
    _library_root,
    _slugify,
)

logger = logging.getLogger(__name__)

TASKS_FILE = ".tasks.yaml"

_DEFAULT_COLUMNS = [
    {"id": "todo", "name": "To Do"},
    {"id": "doing", "name": "Doing"},
    {"id": "done", "name": "Done"},
]

_VALID_CHANNELS = {"all", "sms", "discord", "teamwork"}


# ---------------------------------------------------------------------------
# Paths & I/O
# ---------------------------------------------------------------------------

def _tasks_path(user_id: str, project: str) -> Path:
    return _library_root(user_id) / SPACES_DIR / project / TASKS_FILE


def _project_dir(user_id: str, project: str) -> Path:
    return _library_root(user_id) / SPACES_DIR / project


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read(user_id: str, project: str) -> dict[str, Any] | None:
    """Read .tasks.yaml if the project exists. Returns None if the project
    is missing; seeds the default columns if the file is missing."""
    proj_dir = _project_dir(user_id, project)
    if not proj_dir.exists():
        return None
    path = _tasks_path(user_id, project)
    if not path.exists():
        data = {"columns": list(_DEFAULT_COLUMNS), "tasks": []}
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return data
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        data = {}
    data.setdefault("columns", list(_DEFAULT_COLUMNS))
    data.setdefault("tasks", [])
    return data


def _write(user_id: str, project: str, data: dict) -> None:
    path = _tasks_path(user_id, project)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _append_activity(task: dict, actor: str, action: str, **extra) -> None:
    """Append an entry to a task's activity log."""
    task.setdefault("activity", [])
    entry = {"actor": actor, "at": _now_iso(), "action": action}
    entry.update(extra)
    task["activity"].append(entry)


def _get_project_reminder_channel(user_id: str, project: str) -> str:
    """Return the reminder channel configured on the project meta."""
    from prax.services.library_service import get_space
    proj_meta = get_space(user_id, project) or {}
    return proj_meta.get("reminder_channel", "all")


# ---------------------------------------------------------------------------
# Reminder integration (wraps scheduler_service)
# ---------------------------------------------------------------------------

def _create_reminder_for_task(
    user_id: str,
    project: str,
    task: dict,
) -> str | None:
    """Schedule a reminder for a task.  Returns the reminder_id on success."""
    if not task.get("reminder_enabled", True):
        return None
    due = task.get("due_date")
    if not due:
        return None
    try:
        from prax.services import scheduler_service
        channel = task.get("reminder_channel") or _get_project_reminder_channel(user_id, project)
        if channel not in _VALID_CHANNELS:
            channel = "all"
        description = f"Task due: {task.get('title', '?')}"
        prompt = (
            f"⏰ Task reminder — **{task.get('title', '(untitled)')}**\n"
            f"Project: {project}\n"
            f"Due: {due}\n\n"
            f"{task.get('description', '')}"
        )
        result = scheduler_service.create_reminder(
            user_id=user_id,
            description=description,
            prompt=prompt,
            fire_at=due,
            channel=channel,
        )
        if isinstance(result, dict) and "reminder" in result:
            return result["reminder"].get("id")
        return None
    except Exception:
        logger.exception("Failed to schedule reminder for task %s", task.get("id"))
        return None


def _cancel_reminder_for_task(user_id: str, task: dict) -> None:
    """Cancel an existing reminder for a task."""
    rem_id = task.get("reminder_id")
    if not rem_id:
        return
    try:
        from prax.services import scheduler_service
        scheduler_service.delete_reminder(user_id, rem_id)
    except Exception:
        logger.debug("Could not delete reminder %s (may already be gone)", rem_id)
    task["reminder_id"] = None


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def list_columns(user_id: str, project: str) -> list[dict] | dict:
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    return data["columns"]


def add_column(user_id: str, project: str, name: str) -> dict:
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    col_id = _slugify(name)
    if any(c["id"] == col_id for c in data["columns"]):
        return {"error": f"Column '{col_id}' already exists"}
    col = {"id": col_id, "name": name}
    data["columns"].append(col)
    _write(user_id, project, data)
    return {"status": "added", "column": col}


def rename_column(user_id: str, project: str, column_id: str, new_name: str) -> dict:
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    for c in data["columns"]:
        if c["id"] == column_id:
            c["name"] = new_name
            _write(user_id, project, data)
            return {"status": "renamed", "column": c}
    return {"error": f"Column '{column_id}' not found"}


def remove_column(user_id: str, project: str, column_id: str) -> dict:
    """Delete a column.  Refuses if any tasks are still in it."""
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    tasks_in_col = [t for t in data["tasks"] if t.get("column") == column_id]
    if tasks_in_col:
        return {
            "error": (
                f"Column '{column_id}' still has {len(tasks_in_col)} task(s). "
                "Move them out before deleting."
            )
        }
    before = len(data["columns"])
    data["columns"] = [c for c in data["columns"] if c["id"] != column_id]
    if len(data["columns"]) == before:
        return {"error": f"Column '{column_id}' not found"}
    _write(user_id, project, data)
    return {"status": "removed", "column": column_id}


def reorder_columns(user_id: str, project: str, order: list[str]) -> dict:
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    by_id = {c["id"]: c for c in data["columns"]}
    new_cols: list[dict] = []
    for col_id in order:
        if col_id in by_id:
            new_cols.append(by_id.pop(col_id))
    # Anything not listed goes at the end, stable order
    new_cols.extend(by_id.values())
    data["columns"] = new_cols
    _write(user_id, project, data)
    return {"status": "reordered", "columns": data["columns"]}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def list_tasks(
    user_id: str,
    project: str,
    column: str | None = None,
) -> list[dict] | dict:
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    tasks = data["tasks"]
    if column:
        tasks = [t for t in tasks if t.get("column") == column]
    return tasks


def get_task(user_id: str, project: str, task_id: str) -> dict | None:
    data = _read(user_id, project)
    if data is None:
        return None
    for t in data["tasks"]:
        if t.get("id") == task_id:
            return t
    return None


_VALID_SOURCES = {"user_request", "agent_derived", "tool_output"}
_VALID_CONFIDENCE = {"low", "medium", "high"}


def create_task(
    user_id: str,
    project: str,
    *,
    title: str,
    description: str = "",
    column: str = "todo",
    author: str = "human",
    assignees: list[str] | None = None,
    due_date: str | None = None,
    reminder_enabled: bool = True,
    reminder_channel: str | None = None,
    checklist: list[dict] | None = None,
    source: str = "user_request",
    source_justification: str = "",
    confidence: str = "medium",
) -> dict:
    """Create a new task. Automatically schedules a reminder if a due_date
    is provided and reminder_enabled is true.

    **Provenance (``source``)** — every task records where the request
    to create it came from:

    - ``user_request`` — the human explicitly asked for this task.
      Default when the UI is the caller (``POST /library/notes``).
    - ``agent_derived`` — Prax added the task as part of executing a
      user request.  Default when called from ``library_task_add``.
    - ``tool_output`` — a third-party tool suggested this task (e.g.,
      a calendar integration returned a "follow up with Alice" item,
      or a scraped webpage contained instruction-like text).  This
      is the dangerous case — it's an indirect prompt-injection
      attack surface per docs/research/agentic-todo-flows.md §26.
      Requires ``source_justification`` so the audit trail explains
      what tool produced it and why it's being added.

    See docs/research/prax-changes-from-todo-research.md (P1) for
    the full rationale.
    """
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    if author not in ("human", "prax"):
        return {"error": f"Invalid author '{author}'"}
    if source not in _VALID_SOURCES:
        return {"error": f"Invalid source '{source}'. Use one of {sorted(_VALID_SOURCES)}"}
    if source == "tool_output" and not source_justification.strip():
        return {
            "error": (
                "source=tool_output requires source_justification explaining "
                "which tool produced the task suggestion and why it's being "
                "added to the user's board. This prevents silent "
                "prompt-injection via tool outputs."
            )
        }
    if confidence not in _VALID_CONFIDENCE:
        confidence = "medium"
    if column and not any(c["id"] == column for c in data["columns"]):
        return {"error": f"Column '{column}' does not exist"}

    task_id = f"tsk-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    task: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "description": description,
        "column": column,
        "author": author,
        "source": source,
        "source_justification": source_justification.strip(),
        "confidence": confidence,
        "assignees": assignees or [],
        "due_date": due_date or "",
        "reminder_enabled": bool(reminder_enabled),
        "reminder_id": None,
        "reminder_channel": reminder_channel or "",
        "checklist": checklist or [],
        "activity": [],
        "comments": [],
        "created_at": now,
        "updated_at": now,
    }
    _append_activity(task, actor=author, action="created", source=source)

    if due_date:
        rem_id = _create_reminder_for_task(user_id, project, task)
        if rem_id:
            task["reminder_id"] = rem_id

    data["tasks"].append(task)
    _write(user_id, project, data)
    logger.info("library_tasks: created %s in %s/%s", task_id, project, column)
    return {"status": "created", "task": task}


def update_task(
    user_id: str,
    project: str,
    task_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    due_date: str | None = None,
    reminder_enabled: bool | None = None,
    reminder_channel: str | None = None,
    assignees: list[str] | None = None,
    checklist: list[dict] | None = None,
    confidence: str | None = None,
    editor: str = "human",
) -> dict:
    """Update any subset of mutable task fields. Reschedules the reminder
    if due_date / reminder_enabled / reminder_channel changed."""
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
    if task is None:
        return {"error": f"Task '{task_id}' not found"}

    changed: list[str] = []
    reminder_dirty = False

    if title is not None and title != task.get("title"):
        task["title"] = title
        changed.append("title")
        reminder_dirty = True  # reminder message carries title
    if description is not None and description != task.get("description"):
        task["description"] = description
        changed.append("description")
        reminder_dirty = True
    if due_date is not None and due_date != task.get("due_date"):
        task["due_date"] = due_date
        changed.append("due_date")
        reminder_dirty = True
    if reminder_enabled is not None and reminder_enabled != task.get("reminder_enabled"):
        task["reminder_enabled"] = bool(reminder_enabled)
        changed.append("reminder_enabled")
        reminder_dirty = True
    if reminder_channel is not None and reminder_channel != task.get("reminder_channel"):
        task["reminder_channel"] = reminder_channel
        changed.append("reminder_channel")
        reminder_dirty = True
    if assignees is not None and assignees != task.get("assignees"):
        task["assignees"] = assignees
        changed.append("assignees")
    if checklist is not None:
        task["checklist"] = checklist
        changed.append("checklist")
    if confidence is not None and confidence in _VALID_CONFIDENCE and confidence != task.get("confidence"):
        task["confidence"] = confidence
        changed.append("confidence")

    if not changed:
        return {"status": "unchanged", "task": task}

    if reminder_dirty:
        _cancel_reminder_for_task(user_id, task)
        if task.get("due_date") and task.get("reminder_enabled"):
            rem_id = _create_reminder_for_task(user_id, project, task)
            if rem_id:
                task["reminder_id"] = rem_id

    task["updated_at"] = _now_iso()
    _append_activity(task, actor=editor, action="updated", fields=changed)
    _write(user_id, project, data)
    return {"status": "updated", "task": task, "changed": changed}


def move_task(
    user_id: str,
    project: str,
    task_id: str,
    new_column: str,
    *,
    editor: str = "human",
) -> dict:
    """Move a task to a different column. Cancels the reminder if the
    destination column is a terminal 'done' state."""
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    if not any(c["id"] == new_column for c in data["columns"]):
        return {"error": f"Column '{new_column}' does not exist"}
    task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
    if task is None:
        return {"error": f"Task '{task_id}' not found"}

    old_col = task.get("column")
    if old_col == new_column:
        return {"status": "unchanged", "task": task}

    task["column"] = new_column
    task["updated_at"] = _now_iso()
    _append_activity(task, actor=editor, action="moved", **{"from": old_col, "to": new_column})

    # When moving to a "done"-like column (id == 'done'), cancel the reminder
    # so the user doesn't get pinged about something they've already finished.
    if new_column == "done":
        _cancel_reminder_for_task(user_id, task)

    _write(user_id, project, data)
    return {"status": "moved", "task": task}


def delete_task(
    user_id: str,
    project: str,
    task_id: str,
) -> dict:
    """Delete a task and cancel any pending reminder."""
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
    if task is None:
        return {"error": f"Task '{task_id}' not found"}
    _cancel_reminder_for_task(user_id, task)
    data["tasks"] = [t for t in data["tasks"] if t.get("id") != task_id]
    _write(user_id, project, data)
    return {"status": "deleted", "task_id": task_id}


def add_comment(
    user_id: str,
    project: str,
    task_id: str,
    text: str,
    *,
    actor: str = "human",
) -> dict:
    """Append a comment to a task (also logged in activity)."""
    data = _read(user_id, project)
    if data is None:
        return {"error": f"Project '{project}' not found"}
    task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
    if task is None:
        return {"error": f"Task '{task_id}' not found"}

    comment = {"actor": actor, "at": _now_iso(), "text": text}
    task.setdefault("comments", []).append(comment)
    _append_activity(task, actor=actor, action="commented", text=text[:120])
    task["updated_at"] = _now_iso()
    _write(user_id, project, data)
    return {"status": "commented", "comment": comment}


# ---------------------------------------------------------------------------
# Summary helpers (used by Home dashboard + project view)
# ---------------------------------------------------------------------------

def task_summary(user_id: str, project: str) -> dict:
    """Return per-column counts for the project's Kanban."""
    data = _read(user_id, project)
    if data is None:
        return {"columns": [], "total": 0, "by_column": {}}
    by_col: dict[str, int] = {}
    for t in data["tasks"]:
        col = t.get("column", "todo")
        by_col[col] = by_col.get(col, 0) + 1
    return {
        "columns": data["columns"],
        "total": len(data["tasks"]),
        "by_column": by_col,
    }
