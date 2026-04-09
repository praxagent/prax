"""TeamWork webhook — receives user messages from TeamWork's UI."""
from __future__ import annotations

import hashlib
import logging
import threading

from flask import Blueprint, Flask, jsonify, request

logger = logging.getLogger(__name__)

teamwork_routes = Blueprint("teamwork", __name__)


@teamwork_routes.route("/teamwork/observability", methods=["GET"])
def observability_config():
    """Return observability URLs for the TeamWork frontend.

    TeamWork polls this once on load to know where Grafana/Tempo live
    and whether the trace button should be shown.
    """
    from prax.settings import settings
    return jsonify({
        "enabled": settings.observability_enabled,
        "grafana_url": settings.grafana_url or None,
        "tempo_url": settings.grafana_url + "/explore" if settings.grafana_url else None,
    })


@teamwork_routes.route("/teamwork/sync-history", methods=["POST"])
def sync_history():
    """Sync historical SMS/Discord conversations to TeamWork channels.

    Reads from conversations.db and bulk-imports into #sms and #discord
    channels. Skips channels that already have messages (idempotent).

    Pass ``?force=true`` to clear existing messages and re-sync.
    """
    try:
        from prax.services.teamwork_service import get_teamwork_client
        tw = get_teamwork_client()
        if not tw.enabled or not tw.project_id:
            return jsonify({"error": "TeamWork not connected"}), 503
        force = request.args.get("force", "").lower() in ("true", "1", "yes")
        result = tw.sync_conversation_history(force=force)
        return jsonify({"synced": result})
    except Exception:
        logger.exception("Failed to sync history")
        return jsonify({"error": "sync failed"}), 500


@teamwork_routes.route("/teamwork/webhook", methods=["POST"])
def teamwork_webhook():
    """Receive a user message from TeamWork and process it asynchronously."""
    data = request.get_json(silent=True) or {}
    msg_type = data.get("type", "")
    content = data.get("content", "")
    channel_id = data.get("channel_id", "")
    project_id = data.get("project_id", "")
    message_id = data.get("message_id", "")
    active_view = data.get("active_view", "")
    extra_data = data.get("extra_data") or {}

    if msg_type != "user_message" or not content:
        return jsonify({"status": "ignored"}), 200

    logger.info(
        "TeamWork webhook: project=%s channel=%s view=%s content=%s",
        project_id, channel_id, active_view, content[:80],
    )

    # Process asynchronously so we return 200 quickly.
    # Pass the Flask app so the thread can push an app context.
    from flask import current_app
    app = current_app._get_current_object()

    thread = threading.Thread(
        target=_handle_message,
        args=(app, project_id, channel_id, content, message_id, active_view, extra_data),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "accepted"}), 200


def _build_trace_metadata() -> dict | None:
    """Build trace metadata to attach to the agent's response message.

    Always includes the trace_id (for the Execution Graphs panel).
    Optionally includes a Grafana deep-link if observability is enabled.

    Tries the ContextVar first (most accurate), then falls back to the
    last completed graph (robust across thread/async boundaries).
    """
    from prax.agent.trace import get_last_completed_graph, last_root_trace_id

    trace_id = last_root_trace_id.get()
    if not trace_id:
        # ContextVar may not propagate across thread boundaries —
        # fall back to the last completed graph stored at module scope.
        graph = get_last_completed_graph()
        if graph:
            trace_id = graph.trace_id

    if not trace_id:
        logger.debug("No trace_id available for response metadata")
        return None

    metadata: dict = {"trace_id": trace_id}

    from prax.settings import settings
    if settings.observability_enabled and settings.grafana_url:
        metadata["grafana_trace_url"] = (
            f"{settings.grafana_url.rstrip('/')}/explore?"
            f"left=%7B%22datasource%22:%22tempo%22,"
            f"%22queries%22:%5B%7B%22queryType%22:%22traceqlsearch%22,"
            f"%22query%22:%22{trace_id}%22%7D%5D%7D"
        )
    return metadata


def _get_teamwork_user_id() -> str:
    """Return the UUID user identity for TeamWork messages.

    If TEAMWORK_USER_PHONE is configured, resolves via the identity service
    so the user shares history/workspace with SMS and Discord.
    Falls back to a default "teamwork" provider identity.
    """
    from prax.services.identity_service import resolve_user
    from prax.settings import settings

    if settings.teamwork_user_phone:
        user = resolve_user("sms", settings.teamwork_user_phone)
    else:
        user = resolve_user("teamwork", "default", display_name="TeamWork User")
    return user.id


_VIEW_LABELS = {
    "chat": "the chat tab",
    "browser": "the browser panel (they can see the live browser)",
    "terminal": "the terminal tab",
    "execution_graphs": "the execution graphs tab",
    "observability": "the observability/tracing tab",
    "tasks": "the task board",
    "files": "the file browser",
    "memory": "the memory panel (they can inspect and edit short-term and long-term memory)",
    "settings": "the settings page",
    "progress": "the progress/coaching tab",
    "library": "Library — projects, notebooks, notes, and tasks",
    "home": "Home — active projects dashboard",
}


# ---------------------------------------------------------------------------
# Library API — Project → Notebook → Note hierarchy (see docs/library.md)
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/library", methods=["GET"])
def library_tree():
    """Return the full library tree: projects → notebooks → notes metadata."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify(library_service.get_tree(user_id))
    except Exception:
        logger.exception("Failed to fetch library tree")
        return jsonify({"error": "Failed to fetch library tree"}), 500


@teamwork_routes.route("/teamwork/library/spaces", methods=["POST"])
def library_create_space():
    """Create a new space."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.create_space(
            user_id,
            name=data.get("name", ""),
            description=data.get("description", ""),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to create library space")
        return jsonify({"error": "Failed to create space"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>", methods=["DELETE"])
def library_delete_space(space: str):
    """Delete an empty space."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        result = library_service.delete_space(user_id, space)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete library space")
        return jsonify({"error": "Failed to delete space"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>/notebooks", methods=["POST"])
def library_create_notebook(space: str):
    """Create a new notebook inside a project."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.create_notebook(
            user_id,
            project=project,
            name=data.get("name", ""),
            description=data.get("description", ""),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to create notebook")
        return jsonify({"error": "Failed to create notebook"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/notebooks/<notebook>",
    methods=["DELETE"],
)
def library_delete_notebook(space: str, notebook: str):
    """Delete an empty notebook."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        result = library_service.delete_notebook(user_id, project, notebook)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete notebook")
        return jsonify({"error": "Failed to delete notebook"}), 500


@teamwork_routes.route("/teamwork/library/notes", methods=["POST"])
def library_create_note():
    """Create a note. Defaults to author=human because this endpoint is
    called by the UI — Prax uses the library_note_create tool instead."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.create_note(
            user_id,
            title=data.get("title", ""),
            content=data.get("content", ""),
            project=data.get("project", ""),
            notebook=data.get("notebook", ""),
            author=data.get("author", "human"),
            tags=data.get("tags") or None,
            prax_may_edit=data.get("prax_may_edit"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to create library note")
        return jsonify({"error": "Failed to create note"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>",
    methods=["GET"],
)
def library_get_note(space: str, notebook: str, slug: str):
    """Return a note with metadata and full content."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        note = library_service.get_note(user_id, project, notebook, slug)
        if note is None:
            return jsonify({"error": "Note not found"}), 404
        return jsonify(note)
    except Exception:
        logger.exception("Failed to fetch library note")
        return jsonify({"error": "Failed to fetch note"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>",
    methods=["PATCH"],
)
def library_update_note(space: str, notebook: str, slug: str):
    """Update a note. The UI always calls with editor='human' by default,
    and passes override_permission=True when the human explicitly clicks
    'Ask Prax to refine'."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.update_note(
            user_id,
            project=project,
            notebook=notebook,
            slug=slug,
            content=data.get("content"),
            title=data.get("title"),
            tags=data.get("tags"),
            editor=data.get("editor", "human"),
            override_permission=bool(data.get("override_permission", False)),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to update library note")
        return jsonify({"error": "Failed to update note"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>",
    methods=["DELETE"],
)
def library_delete_note(space: str, notebook: str, slug: str):
    """Delete a note."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        result = library_service.delete_note(user_id, project, notebook, slug)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete library note")
        return jsonify({"error": "Failed to delete note"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/move",
    methods=["PATCH"],
)
def library_move_note(space: str, notebook: str, slug: str):
    """Move a note to a different notebook (and optionally project)."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.move_note(
            user_id,
            from_project=project,
            from_notebook=notebook,
            slug=slug,
            to_project=data.get("to_project", project),
            to_notebook=data.get("to_notebook", ""),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to move library note")
        return jsonify({"error": "Failed to move note"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/editable",
    methods=["PATCH"],
)
def library_set_note_editable(space: str, notebook: str, slug: str):
    """Toggle the prax_may_edit flag on a note."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.set_prax_may_edit(
            user_id, project, notebook, slug, bool(data.get("editable", False)),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to toggle library note editable flag")
        return jsonify({"error": "Failed to update flag"}), 500


# ---------------------------------------------------------------------------
# Agent plan — Prax's private working-memory to-do list
# ---------------------------------------------------------------------------
#
# The agent_plan is Prax's internal stepwise working memory for multi-turn
# tool-use sequences.  It is NOT the Library Kanban — that one is for the
# user's project management.  See docs/library.md for the full wall
# between the two systems.
#
# This endpoint exposes the current plan read-only so the TeamWork chat
# view can render a small "Currently working on" widget giving the user
# situational awareness without the cost of a full Kanban or the risks of
# mid-execution oversight (see docs/research/agentic-todo-flows.md §22).

@teamwork_routes.route("/teamwork/agent-plan", methods=["GET"])
def get_agent_plan():
    """Return Prax's current agent_plan (or null if none active).

    The response is a light denormalization over the raw plan YAML so
    the chat widget doesn't have to re-compute progress: it includes
    ``goal``, ``steps`` (with per-step ``done`` flags), ``done_count``,
    ``total``, and the ``current_step`` object (the first not-done step,
    or null if every step is done).
    """
    try:
        from prax.services import workspace_service
        user_id = _get_teamwork_user_id()
        plan = workspace_service.read_plan(user_id)
        if not plan:
            return jsonify(None)
        steps = plan.get("steps", [])
        done_count = sum(1 for s in steps if s.get("done"))
        current = next((s for s in steps if not s.get("done")), None)
        return jsonify({
            "id": plan.get("id"),
            "goal": plan.get("goal", ""),
            "confidence": plan.get("confidence", "medium"),
            "steps": steps,
            "done_count": done_count,
            "total": len(steps),
            "current_step": current,
            "created_at": plan.get("created_at"),
        })
    except Exception:
        logger.exception("Failed to read agent plan")
        return jsonify({"error": "Failed to read agent plan"}), 500


# ---------------------------------------------------------------------------
# Library Phase 3 — project metadata, notebook sequencing, Kanban tasks
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/library/spaces/<space>", methods=["GET"])
def library_get_space(space: str):
    """Return full space metadata + progress."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        meta = library_service.get_space(user_id, space)
        if meta is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(meta)
    except Exception:
        logger.exception("Failed to fetch space")
        return jsonify({"error": "Failed to fetch project"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>/cover", methods=["GET"])
def library_get_space_cover(space: str):
    """Serve the raw cover image for a space."""
    try:
        from flask import send_file

        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        path = library_service.get_space_cover_path(user_id, space)
        if path is None:
            return jsonify({"error": "No cover image"}), 404
        return send_file(str(path))
    except Exception:
        logger.exception("Failed to serve space cover")
        return jsonify({"error": "Failed to serve cover"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>/cover", methods=["POST"])
def library_upload_space_cover(space: str):
    """Upload a user-provided cover image for a space (multipart)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "Empty filename"}), 400
        # Extract extension from filename
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else "png"
        data = f.read()
        # Size cap at 10 MB
        if len(data) > 10 * 1024 * 1024:
            return jsonify({"error": "Cover image too large (max 10 MB)"}), 413
        result = library_service.save_space_cover(user_id, space, data, ext)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to upload space cover")
        return jsonify({"error": "Failed to upload cover"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>/cover", methods=["DELETE"])
def library_delete_space_cover(space: str):
    """Remove the cover image for a space."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        result = library_service.delete_space_cover(user_id, space)
        if "error" in result:
            return jsonify({"error": result["error"]}), 404
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete space cover")
        return jsonify({"error": "Failed to delete cover"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>/cover/generate", methods=["POST"])
def library_generate_space_cover(space: str):
    """Ask Prax to generate a cover image for a space."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.generate_space_cover(
            user_id, space, prompt_hint=data.get("prompt_hint", ""),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to generate space cover")
        return jsonify({"error": "Failed to generate cover"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>", methods=["PATCH"])
def library_update_space(space: str):
    """Update space metadata (status, kind, description, etc.)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.update_space(
            user_id,
            space,
            name=data.get("name"),
            description=data.get("description"),
            kind=data.get("kind"),
            status=data.get("status"),
            target_date=data.get("target_date"),
            pinned=data.get("pinned"),
            tasks_enabled=data.get("tasks_enabled"),
            reminder_channel=data.get("reminder_channel"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to update space")
        return jsonify({"error": "Failed to update space"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/notebooks/<notebook>",
    methods=["PATCH"],
)
def library_update_notebook(space: str, notebook: str):
    """Update notebook metadata (sequenced toggle, current lesson, rename)."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.update_notebook(
            user_id,
            project,
            notebook,
            name=data.get("name"),
            description=data.get("description"),
            sequenced=data.get("sequenced"),
            current_slug=data.get("current_slug"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to update notebook")
        return jsonify({"error": "Failed to update notebook"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/notebooks/<notebook>/reorder",
    methods=["POST"],
)
def library_reorder_notebook(space: str, notebook: str):
    """Batch-reorder notes in a sequenced notebook."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        slug_order = data.get("slug_order", [])
        if not isinstance(slug_order, list):
            return jsonify({"error": "slug_order must be a list"}), 400
        result = library_service.reorder_notes(user_id, project, notebook, slug_order)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to reorder notes")
        return jsonify({"error": "Failed to reorder"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/status",
    methods=["PATCH"],
)
def library_set_note_status(space: str, notebook: str, slug: str):
    """Mark a note as todo or done."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.set_note_status(
            user_id, project, notebook, slug, data.get("status", "todo"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to set note status")
        return jsonify({"error": "Failed to set status"}), 500


# --- Tasks ---

@teamwork_routes.route("/teamwork/library/spaces/<space>/tasks", methods=["GET"])
def library_list_tasks(space: str):
    """List tasks for a project, optionally filtered by ?column=..."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        column = request.args.get("column")
        result = library_tasks.list_tasks(user_id, project, column)
        if isinstance(result, dict) and "error" in result:
            return jsonify(result), 404
        return jsonify({"tasks": result})
    except Exception:
        logger.exception("Failed to list tasks")
        return jsonify({"error": "Failed to list tasks"}), 500


@teamwork_routes.route("/teamwork/library/spaces/<space>/tasks", methods=["POST"])
def library_create_task(space: str):
    """Create a new task. Defaults author to 'human' and source to
    'user_request' because the UI is the caller — a human clicking
    'Add task' is by definition an explicit user request."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.create_task(
            user_id,
            project,
            title=data.get("title", ""),
            description=data.get("description", ""),
            column=data.get("column", "todo"),
            author=data.get("author", "human"),
            assignees=data.get("assignees"),
            due_date=data.get("due_date"),
            reminder_enabled=data.get("reminder_enabled", True),
            reminder_channel=data.get("reminder_channel"),
            checklist=data.get("checklist"),
            source=data.get("source", "user_request"),
            source_justification=data.get("source_justification", ""),
            confidence=data.get("confidence", "medium"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to create task")
        return jsonify({"error": "Failed to create task"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/<task_id>",
    methods=["GET"],
)
def library_get_task(space: str, task_id: str):
    """Return a single task with full details (activity log, comments)."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        task = library_tasks.get_task(user_id, project, task_id)
        if task is None:
            return jsonify({"error": "Task not found"}), 404
        return jsonify(task)
    except Exception:
        logger.exception("Failed to fetch task")
        return jsonify({"error": "Failed to fetch task"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/<task_id>",
    methods=["PATCH"],
)
def library_update_task(space: str, task_id: str):
    """Update a task."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.update_task(
            user_id,
            project,
            task_id,
            title=data.get("title"),
            description=data.get("description"),
            due_date=data.get("due_date"),
            reminder_enabled=data.get("reminder_enabled"),
            reminder_channel=data.get("reminder_channel"),
            assignees=data.get("assignees"),
            checklist=data.get("checklist"),
            confidence=data.get("confidence"),
            editor=data.get("editor", "human"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to update task")
        return jsonify({"error": "Failed to update task"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/<task_id>",
    methods=["DELETE"],
)
def library_delete_task(space: str, task_id: str):
    """Delete a task."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        result = library_tasks.delete_task(user_id, project, task_id)
        if "error" in result:
            return jsonify({"error": result["error"]}), 404
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete task")
        return jsonify({"error": "Failed to delete task"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/<task_id>/move",
    methods=["PATCH"],
)
def library_move_task(space: str, task_id: str):
    """Move a task to a different column."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.move_task(
            user_id, project, task_id, data.get("column", ""),
            editor=data.get("editor", "human"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to move task")
        return jsonify({"error": "Failed to move task"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/<task_id>/comment",
    methods=["POST"],
)
def library_comment_task(space: str, task_id: str):
    """Add a comment to a task."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.add_comment(
            user_id, project, task_id,
            text=data.get("text", ""),
            actor=data.get("actor", "human"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to comment on task")
        return jsonify({"error": "Failed to comment"}), 500


# --- Columns ---

@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/columns",
    methods=["GET"],
)
def library_list_columns(space: str):
    """List Kanban columns for a project."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        result = library_tasks.list_columns(user_id, project)
        if isinstance(result, dict) and "error" in result:
            return jsonify(result), 404
        return jsonify({"columns": result})
    except Exception:
        logger.exception("Failed to list columns")
        return jsonify({"error": "Failed to list columns"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/columns",
    methods=["POST"],
)
def library_add_column(space: str):
    """Add a new column to the project's Kanban board."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.add_column(user_id, project, data.get("name", ""))
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to add column")
        return jsonify({"error": "Failed to add column"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/columns/<column_id>",
    methods=["PATCH"],
)
def library_rename_column(space: str, column_id: str):
    """Rename a column."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.rename_column(user_id, project, column_id, data.get("name", ""))
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to rename column")
        return jsonify({"error": "Failed to rename column"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/columns/<column_id>",
    methods=["DELETE"],
)
def library_remove_column(space: str, column_id: str):
    """Remove a column (refuses if tasks still in it)."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        result = library_tasks.remove_column(user_id, project, column_id)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to remove column")
        return jsonify({"error": "Failed to remove column"}), 500


@teamwork_routes.route(
    "/teamwork/library/spaces/<space>/tasks/columns/reorder",
    methods=["POST"],
)
def library_reorder_columns(space: str):
    """Reorder columns."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_tasks
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_tasks.reorder_columns(user_id, project, data.get("order", []))
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to reorder columns")
        return jsonify({"error": "Failed to reorder columns"}), 500


# ---------------------------------------------------------------------------
# Library Phase 2 — schema, index, backlinks, refine, raw, outputs, health
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/library/schema", methods=["GET"])
def library_get_schema():
    """Return LIBRARY.md content."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify({"content": library_service.read_schema(user_id)})
    except Exception:
        logger.exception("Failed to read library schema")
        return jsonify({"error": "Failed to read schema"}), 500


@teamwork_routes.route("/teamwork/library/schema", methods=["PUT"])
def library_put_schema():
    """Overwrite LIBRARY.md with new content."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.write_schema(user_id, data.get("content", ""))
        return jsonify(result)
    except Exception:
        logger.exception("Failed to write library schema")
        return jsonify({"error": "Failed to write schema"}), 500


@teamwork_routes.route("/teamwork/library/tags", methods=["GET"])
def library_tag_tree():
    """Return the nested tag tree (e.g., math/algebra/linear)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify(library_service.list_tag_tree(user_id))
    except Exception:
        logger.exception("Failed to fetch tag tree")
        return jsonify({"error": "Failed to fetch tag tree"}), 500


@teamwork_routes.route("/teamwork/library/notes/by-tag", methods=["GET"])
def library_notes_by_tag():
    """Return notes matching a tag prefix (?prefix=math/algebra)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        prefix = request.args.get("prefix", "")
        return jsonify({
            "notes": library_service.list_notes_by_tag_prefix(user_id, prefix),
        })
    except Exception:
        logger.exception("Failed to fetch notes by tag")
        return jsonify({"error": "Failed to fetch notes by tag"}), 500


@teamwork_routes.route("/teamwork/library/index", methods=["GET"])
def library_get_index():
    """Return the auto-maintained INDEX.md content."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify({"content": library_service.read_index(user_id)})
    except Exception:
        logger.exception("Failed to read library index")
        return jsonify({"error": "Failed to read index"}), 500


@teamwork_routes.route("/teamwork/library/index/rebuild", methods=["POST"])
def library_rebuild_index():
    """Force-rebuild the auto-maintained INDEX.md.

    The index is normally rebuilt on every write operation (note
    create/update/delete, notebook/project changes).  This endpoint is
    for the manual "Rebuild" button in the UI — useful when the index
    drifts from disk (e.g., after a git checkout, a manual edit
    outside the app, or a migration).  Returns the freshly-written
    content so the caller can update its cached view without a
    follow-up GET.
    """
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        library_service.rebuild_index(user_id)
        return jsonify({
            "status": "rebuilt",
            "content": library_service.read_index(user_id),
        })
    except Exception:
        logger.exception("Failed to rebuild library index")
        return jsonify({"error": "Failed to rebuild index"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/backlinks",
    methods=["GET"],
)
def library_note_backlinks(space: str, notebook: str, slug: str):
    """Return notes that link to this note via [[wikilinks]]."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        backlinks = library_service.get_backlinks(user_id, project, notebook, slug)
        return jsonify({"backlinks": backlinks})
    except Exception:
        logger.exception("Failed to fetch backlinks")
        return jsonify({"error": "Failed to fetch backlinks"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/refine",
    methods=["POST"],
)
def library_note_refine(space: str, notebook: str, slug: str):
    """Generate a refined version of a note. Returns before/after
    without applying — the UI approves and calls /apply-refine."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.refine_note(
            user_id, project, notebook, slug,
            instructions=data.get("instructions", ""),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to refine note")
        return jsonify({"error": "Failed to refine"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/refine-via-agent",
    methods=["POST"],
)
def library_note_refine_via_agent(space: str, notebook: str, slug: str):
    """Queue a refinement request through the full chat agent.

    Unlike /refine (which uses a direct low-tier LLM call and returns
    before/after for preview), this endpoint adds the note to the
    pending-engagement queue and triggers a normal chat reply — so the
    agent can use tools (web search, arxiv lookup, citation finder,
    knowledge_ingest) when the refinement needs them.

    The body is ``{instructions: str}``.  Returns the synchronous
    agent reply, same shape as a normal chat turn.
    """
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        instructions = (data.get("instructions") or "").strip()
        if not instructions:
            return jsonify({"error": "Missing 'instructions'"}), 400

        # Verify the note exists and read it for context.
        note = library_service.get_note(user_id, project, notebook, slug)
        if note is None:
            return jsonify({"error": "Note not found"}), 404

        # Unlock the note for Prax for this turn (human-initiated refine
        # counts as explicit consent).  Idempotent.
        library_service.set_prax_may_edit(user_id, project, notebook, slug, True)

        # Compose the message that flows through the normal agent loop.
        prompt = (
            f"[User asked you to refine a library note via the UI.]\n\n"
            f"Note path: `{project}/{notebook}/{slug}`\n"
            f"Title: {note['meta'].get('title', slug)}\n"
            f"Instructions: {instructions}\n\n"
            f"=== CURRENT NOTE BODY ===\n{note['content']}\n=== END ===\n\n"
            "Use any tools you need (web search, arxiv, knowledge_ingest, "
            "library_note_read for related notes, etc.) to improve the note, "
            "then call library_note_update to save the result.  When done, "
            "briefly tell the user what you changed."
        )

        from prax.services.conversation_service import conversation_service
        response = conversation_service.reply(user_id, prompt)
        return jsonify({"status": "ok", "response": response})
    except Exception:
        logger.exception("Failed to refine via agent")
        return jsonify({"error": "Failed to refine via agent"}), 500


@teamwork_routes.route(
    "/teamwork/library/notes/<space>/<notebook>/<slug>/apply-refine",
    methods=["POST"],
)
def library_note_apply_refine(space: str, notebook: str, slug: str):
    """Apply an approved refine result to the note (with override_permission)."""
    project = space  # URL var rename alias (see rename rationale)
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.apply_refine(
            user_id, project, notebook, slug,
            new_content=data.get("content", ""),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to apply refine")
        return jsonify({"error": "Failed to apply refine"}), 500


# --- Raw captures ---

@teamwork_routes.route("/teamwork/library/raw", methods=["GET"])
def library_list_raw():
    """List all raw captures."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify({"raw": library_service.list_raw(user_id)})
    except Exception:
        logger.exception("Failed to list raw")
        return jsonify({"error": "Failed to list raw"}), 500


@teamwork_routes.route("/teamwork/library/raw", methods=["POST"])
def library_capture_raw():
    """Capture a new raw item."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.raw_capture(
            user_id,
            title=data.get("title", "untitled"),
            content=data.get("content", ""),
            source_url=data.get("source_url"),
        )
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to capture raw")
        return jsonify({"error": "Failed to capture raw"}), 500


@teamwork_routes.route("/teamwork/library/raw/<slug>", methods=["GET"])
def library_get_raw(slug: str):
    """Fetch a raw capture."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        item = library_service.get_raw(user_id, slug)
        if item is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(item)
    except Exception:
        logger.exception("Failed to fetch raw")
        return jsonify({"error": "Failed to fetch raw"}), 500


@teamwork_routes.route("/teamwork/library/raw/<slug>", methods=["DELETE"])
def library_delete_raw(slug: str):
    """Delete a raw capture without promoting."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        result = library_service.delete_raw(user_id, slug)
        if "error" in result:
            return jsonify({"error": result["error"]}), 404
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete raw")
        return jsonify({"error": "Failed to delete raw"}), 500


@teamwork_routes.route("/teamwork/library/raw/<slug>/promote", methods=["POST"])
def library_promote_raw(slug: str):
    """Promote a raw capture to a real note inside a notebook."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.promote_raw(
            user_id, slug,
            project=data.get("project", ""),
            notebook=data.get("notebook", ""),
            new_title=data.get("title"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to promote raw")
        return jsonify({"error": "Failed to promote"}), 500


# --- Outputs ---

@teamwork_routes.route("/teamwork/library/outputs", methods=["GET"])
def library_list_outputs():
    """List all generated outputs."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify({"outputs": library_service.list_outputs(user_id)})
    except Exception:
        logger.exception("Failed to list outputs")
        return jsonify({"error": "Failed to list outputs"}), 500


@teamwork_routes.route("/teamwork/library/outputs/<slug>", methods=["GET"])
def library_get_output(slug: str):
    """Fetch a generated output."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        item = library_service.get_output(user_id, slug)
        if item is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(item)
    except Exception:
        logger.exception("Failed to fetch output")
        return jsonify({"error": "Failed to fetch output"}), 500


# --- Archive (long-term keepers: PDFs, docs, reference material) ---

@teamwork_routes.route("/teamwork/library/archive", methods=["GET"])
def library_list_archive():
    """List all archived documents (newest first)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify({"archive": library_service.list_archive(user_id)})
    except Exception:
        logger.exception("Failed to list archive")
        return jsonify({"error": "Failed to list archive"}), 500


@teamwork_routes.route("/teamwork/library/archive", methods=["POST"])
def library_capture_archive():
    """Archive a new document (extracted markdown + metadata)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.archive_capture(
            user_id,
            title=data.get("title", "untitled"),
            content=data.get("content", ""),
            source_url=data.get("source_url"),
            source_filename=data.get("source_filename"),
            binary_path=data.get("binary_path"),
            tags=data.get("tags") or [],
        )
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to archive document")
        return jsonify({"error": "Failed to archive"}), 500


@teamwork_routes.route("/teamwork/library/archive/<slug>", methods=["GET"])
def library_get_archive(slug: str):
    """Fetch an archived document (meta + extracted markdown)."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        item = library_service.get_archive(user_id, slug)
        if item is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(item)
    except Exception:
        logger.exception("Failed to fetch archive entry")
        return jsonify({"error": "Failed to fetch archive entry"}), 500


@teamwork_routes.route("/teamwork/library/archive/<slug>", methods=["DELETE"])
def library_delete_archive(slug: str):
    """Delete an archive entry."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        result = library_service.delete_archive(user_id, slug)
        if "error" in result:
            return jsonify({"error": result["error"]}), 404
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete archive entry")
        return jsonify({"error": "Failed to delete archive entry"}), 500


# --- Health check ---

@teamwork_routes.route("/teamwork/library/health-check", methods=["POST"])
def library_run_health_check():
    """Run the Karpathy-style monthly audit and return the report."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        return jsonify(library_service.run_health_check(user_id))
    except Exception:
        logger.exception("Failed to run health check")
        return jsonify({"error": "Failed to run health check"}), 500


@teamwork_routes.route("/teamwork/library/health-check/schedule", methods=["POST"])
def library_schedule_health_check():
    """Schedule the health check on a recurring cron."""
    try:
        from prax.services import library_service
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        result = library_service.schedule_health_check(
            user_id,
            cron_expr=data.get("cron_expr", "0 9 * * 1"),
            channel=data.get("channel", "all"),
            timezone=data.get("timezone"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to schedule health check")
        return jsonify({"error": "Failed to schedule health check"}), 500


def _handle_claude_code_interjection(tw, content: str, channel_id: str) -> bool:
    """Handle a human message posted in #claude-code.

    If there's an active Claude Code session, the message is forwarded
    directly to Claude Code via the bridge.  Returns True if handled.

    If no session is active (or the bridge is down), returns False so
    the caller can fall through to the normal conversation flow — letting
    Prax process the request (e.g. start a session himself).
    """
    from prax.agent.claude_code_tools import _post, is_bridge_available

    if not is_bridge_available():
        return False  # Let Prax handle it through normal conversation

    # Find the active session by querying the bridge.
    try:
        import requests as _req

        from prax.agent.claude_code_tools import _bridge_url
        from prax.agent.claude_code_tools import _headers as _bridge_headers

        resp = _req.get(
            f"{_bridge_url()}/sessions",
            headers=_bridge_headers(),
            timeout=5,
        )
        sessions = resp.json().get("sessions", []) if resp.ok else []
    except Exception:
        sessions = []

    if not sessions:
        return False  # No active session — let Prax handle it normally

    # Use the most recently active session.
    session_id = sessions[0]["session_id"]

    # Post the human's message to the channel for the transcript.
    tw.send_message(
        content=f"**[Human override]** {content}",
        channel="claude-code",
    )

    # Forward to Claude Code via the bridge.
    result = _post("/session/message", {
        "session_id": session_id,
        "message": f"[HUMAN OVERRIDE from the project owner]: {content}",
        "timeout": 300,
    }, timeout=300)

    if "error" in result:
        tw.send_message(
            content=f"Failed to relay to Claude Code: {result['error']}",
            channel="claude-code",
            agent_name="Prax",
        )
        return True

    response = result.get("response", "(no response)")
    tw.send_message(
        content=response,
        channel="claude-code",
        agent_name="Claude Code",
    )
    return True


def _handle_message(
    app: Flask,
    project_id: str,
    channel_id: str,
    content: str,
    message_id: str,
    active_view: str = "",
    extra_data: dict | None = None,
) -> None:
    """Process a TeamWork user message through Prax's conversation service."""
    with app.app_context():
        try:
            from prax.agent.user_context import current_active_view, current_channel_id
            from prax.services.conversation_service import conversation_service
            from prax.services.teamwork_service import get_teamwork_client

            tw = get_teamwork_client()

            user_id = _get_teamwork_user_id()

            # Coding agent channels (#claude-code, #codex, #opencode) — if there's
            # an active session, relay directly.  Otherwise fall through to Prax's
            # normal flow so he can process the request using the coding agent.
            _coding_channels = {"claude-code", "codex", "opencode"}
            is_coding_channel = any(
                tw.get_channel_id(ch) == channel_id
                for ch in _coding_channels
                if tw.get_channel_id(ch)
            )
            if is_coding_channel:
                if _handle_claude_code_interjection(tw, content, channel_id):
                    return

            # Determine if this is a DM or a public channel.
            known_channels = set(tw._channels.values()) if tw._channels else set()
            is_dm = channel_id not in known_channels

            # Build view context — tell the agent which tab the user is on.
            view_label = _VIEW_LABELS.get(active_view, "")
            if view_label:
                view_hint = f"The user is currently viewing {view_label}. "
            else:
                view_hint = ""

            # View-specific tool guidance.
            if active_view == "browser":
                tool_guidance = (
                    "You and the user are PAIRING in a shared live browser — they see "
                    "everything you navigate to in real time via screencast. RULES: "
                    "1) ALWAYS use delegate_browser for ANY web task — navigating URLs, "
                    "reading pages, clicking links, filling forms. The user WATCHES the "
                    "browser as you control it. "
                    "2) NEVER use background_search_tool or fetch_url_content when the "
                    "user asks to visit/open/navigate to a site — those are invisible. "
                    "Use delegate_browser so they see it happen live. "
                    "3) ACT, don't ask. If the user says 'go to hacker news', run "
                    "delegate_browser('navigate to https://news.ycombinator.com'). "
                    "If they say 'open that link', delegate_browser with the URL. "
                    "4) You are pair browsing — be proactive, narrate what you see."
                )
            elif active_view == "terminal":
                tool_guidance = (
                    "You and the user are PAIRING in a shared terminal — they see "
                    "everything you run in real time. RULES: "
                    "1) ALWAYS use sandbox_shell — never delegate_sandbox. "
                    "2) Just RUN commands. Do NOT ask 'what command?' or 'are you sure?' "
                    "or list options. If the user says 'check disk space', run df -h. "
                    "If they say 'list files', run ls -la. ACT, don't ask. "
                    "3) You are an expert pair programmer — infer the right command "
                    "from context and execute it immediately."
                )
            elif active_view == "library":
                # Library view — the user is browsing their hierarchical
                # knowledge base (projects, notebooks, notes, raw, outputs,
                # tasks).  Include the selected item if one is open.
                content_ctx = (extra_data or {}).get("content_context")
                if content_ctx and isinstance(content_ctx, dict):
                    viewing_hint = (
                        f"The user is currently viewing a library item: "
                        f"{content_ctx.get('project', '?')}/"
                        f"{content_ctx.get('notebook', '?')}/"
                        f"{content_ctx.get('slug', '?')} — "
                        f"\"{content_ctx.get('title', '?')}\". "
                        "When they say 'this note' or 'this page', they mean "
                        "this item. "
                    )
                else:
                    viewing_hint = ""
                tool_guidance = (
                    f"The user is browsing the Library. {viewing_hint}"
                    "Use the library_* tools for any reads or writes.  The "
                    "human may be editing notes directly in the UI, so if "
                    "they say '[I just edited this]', treat that as already "
                    "done. Respect the prax_may_edit gate on human-authored "
                    "notes.  For task-related requests use the library_task_* "
                    "tools scoped to the current project."
                )
            elif active_view == "home":
                tool_guidance = (
                    "The user is on the Home dashboard — they see a grid of "
                    "their active spaces with status and progress.  If "
                    "they ask about 'my spaces' or want a status summary, "
                    "use library_spaces_list and library_tasks_list per "
                    "space to gather context."
                )
            elif is_coding_channel:
                tool_guidance = (
                    "The user is posting in a coding agent channel. They expect you "
                    "to use the coding agent (claude_code_start_session) for this task. "
                    "Start a session and collaborate. The transcript will appear in this channel."
                )
            else:
                tool_guidance = (
                    "The user is NOT watching the browser right now, but delegate_browser "
                    "is still available for any task that needs real browser rendering — "
                    "JS-heavy pages, login flows, form filling, sites where fetch_url_content "
                    "returns empty/broken content. Use it freely when HTTP tools fail."
                )

            # Look up channel name and purpose for agent context.
            channel_name = ""
            channel_purpose = ""
            if tw._channels:
                for cname, cid in tw._channels.items():
                    if cid == channel_id:
                        channel_name = cname
                        break
            # Fetch channel description (purpose) from TeamWork API.
            if channel_name and not is_dm:
                try:
                    import requests as _req
                    resp = _req.get(
                        f"{tw.base_url}/api/channels/{channel_id}",
                        headers=tw._headers(),
                        timeout=3,
                    )
                    if resp.ok:
                        channel_purpose = resp.json().get("description") or ""
                except Exception:
                    pass

            channel_ctx = ""
            if channel_name and not is_dm:
                channel_ctx = f"Channel: #{channel_name}"
                if channel_purpose:
                    channel_ctx += f" — Purpose: {channel_purpose}"
                channel_ctx += ". "

            if is_dm:
                channel_hint = (
                    f"[via TeamWork web UI — private DM. {view_hint}{tool_guidance}]\n"
                )
            else:
                channel_hint = (
                    f"[via TeamWork web UI — #{channel_name or 'channel'}. {channel_ctx}{view_hint}{tool_guidance}]\n"
                )

            # When the user is viewing the terminal or browser, fetch context
            # so Prax can "see" what's on the user's screen.
            view_context = ""
            if active_view == "terminal" and tw._project_id:
                try:
                    import requests as _req
                    resp = _req.get(
                        f"{tw.base_url}/api/terminal/{tw._project_id}/recent",
                        headers=tw._headers(),
                        timeout=3,
                    )
                    if resp.ok:
                        lines = resp.json().get("output", "")
                        if lines:
                            view_context = (
                                f"\n[TERMINAL SCREEN — last ~50 lines the user can see right now]\n"
                                f"```\n{lines}\n```\n"
                            )
                except Exception:
                    pass
            elif active_view == "content":
                # Fetch the note content so Prax can discuss it immediately
                content_ctx = (extra_data or {}).get("content_context")
                if content_ctx and isinstance(content_ctx, dict) and content_ctx.get("slug"):
                    try:
                        from prax.services.note_service import get_note
                        note = get_note(user_id, content_ctx["slug"])
                        note_content = note.get("content", "")
                        # Truncate very long notes to avoid bloating the context
                        if len(note_content) > 6000:
                            note_content = note_content[:6000] + "\n\n*[truncated — use note_read for full content]*"
                        note_title = note.get("title", content_ctx.get("title", content_ctx["slug"]))
                        note_tags = ", ".join(note.get("tags", []))
                        view_context = (
                            f"\n[CONTENT PANEL — the user is viewing this note right now]\n"
                            f"Title: {note_title}\n"
                            + (f"Tags: {note_tags}\n" if note_tags else "")
                            + f"```markdown\n{note_content}\n```\n"
                        )
                    except Exception:
                        pass
            elif active_view == "browser":
                try:
                    import requests as _req
                    resp = _req.get(
                        f"{tw.base_url}/api/browser/info",
                        headers=tw._headers(),
                        timeout=3,
                    )
                    if resp.ok:
                        info = resp.json()
                        if info.get("available"):
                            browser_info = info.get("browser", "Chrome")
                            view_context = (
                                f"\n[LIVE BROWSER — the user is watching the screencast right now. "
                                f"Browser: {browser_info}. Use delegate_browser to control it.]\n"
                            )
                except Exception:
                    pass

            prefixed_content = f"{channel_hint}{view_context}{content}"

            # Always set channel context so agent hooks know which channel
            # originated the request (used for response routing).
            from prax.agent.user_context import current_channel_name
            current_channel_id.set(channel_id)
            current_active_view.set(active_view)
            current_channel_name.set(channel_name or ("DM" if is_dm else ""))

            # Derive a per-channel conversation key so each TeamWork channel
            # gets its own isolated conversation history.
            channel_key = int(hashlib.sha256(channel_id.encode()).hexdigest()[:15], 16)

            # Keep typing indicator alive for the entire duration of processing.
            with tw.typing(channel_id=channel_id, agent_name="Prax"):
                response = conversation_service.reply(
                    user_id, prefixed_content, conversation_key=channel_key,
                    trigger=content,  # raw user message, no system prefixes
                )

            # Attach trace metadata so TeamWork can link to the observability stack.
            extra_data = _build_trace_metadata()

            # Send response back to the SAME channel (could be DM, #general, etc.)
            tw.send_message(
                content=response,
                channel_id=channel_id,
                agent_name="Prax",
                extra_data=extra_data,
            )

        except Exception:
            logger.exception("Failed to process TeamWork message")
            try:
                from prax.services.teamwork_service import get_teamwork_client
                tw = get_teamwork_client()
                tw.send_message(
                    content="Sorry, I encountered an error processing your message.",
                    channel_id=channel_id,
                    agent_name="Prax",
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Memory API — STM, LTM, Graph, and stats
# ---------------------------------------------------------------------------

def _memory_service():
    """Return the singleton MemoryService, importing lazily."""
    from prax.services.memory_service import get_memory_service
    return get_memory_service()


def _memory_disabled_response():
    """Standard JSON error when memory is not enabled."""
    return jsonify({"error": "Memory system is not enabled"}), 503


# ---------------------------------------------------------------------------
# Feedback API — thumbs up/down on agent messages
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/feedback", methods=["POST"])
def submit_feedback():
    """Record user feedback (thumbs up/down) on an agent message.

    JSON body: {rating, trace_id?, message_content?, comment?}
    rating: "positive" or "negative"
    """
    try:
        data = request.get_json(silent=True) or {}
        rating = data.get("rating", "").strip()
        if rating not in ("positive", "negative"):
            return jsonify({"error": "rating must be 'positive' or 'negative'"}), 400

        user_id = _get_teamwork_user_id()
        from prax.services.feedback_service import submit_feedback as _submit

        entry = _submit(
            user_id=user_id,
            rating=rating,
            trace_id=data.get("trace_id", ""),
            message_content=data.get("message_content", ""),
            comment=data.get("comment", ""),
        )
        return jsonify({
            "id": entry.id,
            "rating": entry.rating,
            "trace_id": entry.trace_id,
            "created_at": entry.created_at,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        logger.exception("Failed to submit feedback")
        return jsonify({"error": "Failed to submit feedback"}), 500


@teamwork_routes.route("/teamwork/feedback", methods=["GET"])
def list_feedback():
    """Return recent feedback entries.

    Query params: rating=positive|negative, limit=50
    """
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.feedback_service import get_feedback

        rating = request.args.get("rating")
        limit = int(request.args.get("limit", "50"))
        entries = get_feedback(user_id=user_id, rating_filter=rating, limit=limit)
        return jsonify({
            "entries": [
                {
                    "id": e.id,
                    "rating": e.rating,
                    "trace_id": e.trace_id,
                    "message_content": e.message_content[:200],
                    "comment": e.comment,
                    "created_at": e.created_at,
                }
                for e in entries
            ],
        })
    except Exception:
        logger.exception("Failed to list feedback")
        return jsonify({"error": "Failed to list feedback"}), 500


@teamwork_routes.route("/teamwork/feedback/stats", methods=["GET"])
def feedback_stats():
    """Return feedback statistics."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.feedback_service import get_feedback_stats

        return jsonify(get_feedback_stats(user_id=user_id))
    except Exception:
        logger.exception("Failed to get feedback stats")
        return jsonify({"error": "Failed to get feedback stats"}), 500


# ---------------------------------------------------------------------------
# Failure Journal & Eval API
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/failures", methods=["GET"])
def list_failures():
    """Return failure journal entries.

    Query params: resolved=true|false, category=..., limit=50
    """
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.memory.failure_journal import get_failures

        resolved_param = request.args.get("resolved")
        resolved = None
        if resolved_param == "true":
            resolved = True
        elif resolved_param == "false":
            resolved = False

        category = request.args.get("category")
        limit = int(request.args.get("limit", "50"))

        cases = get_failures(
            user_id=user_id, resolved=resolved,
            category=category, limit=limit,
        )
        return jsonify({
            "cases": [
                {
                    "id": c.id,
                    "trace_id": c.trace_id,
                    "user_input": c.user_input[:200],
                    "agent_output": c.agent_output[:200],
                    "feedback_comment": c.feedback_comment,
                    "failure_category": c.failure_category,
                    "tools_involved": c.tools_involved,
                    "resolved": c.resolved,
                    "resolution": c.resolution,
                    "created_at": c.created_at,
                }
                for c in cases
            ],
        })
    except Exception:
        logger.exception("Failed to list failure cases")
        return jsonify({"error": "Failed to list failure cases"}), 500


@teamwork_routes.route("/teamwork/failures/stats", methods=["GET"])
def failure_stats():
    """Return failure journal statistics."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.memory.failure_journal import get_failure_stats

        return jsonify(get_failure_stats(user_id=user_id))
    except Exception:
        logger.exception("Failed to get failure stats")
        return jsonify({"error": "Failed to get failure stats"}), 500


@teamwork_routes.route("/teamwork/failures/<case_id>/resolve", methods=["POST"])
def resolve_failure(case_id: str):
    """Mark a failure case as resolved.

    JSON body: {resolution: "description of what was fixed"}
    """
    try:
        data = request.get_json(silent=True) or {}
        resolution = data.get("resolution", "").strip()
        if not resolution:
            return jsonify({"error": "resolution is required"}), 400

        from prax.services.memory.failure_journal import resolve_failure as _resolve

        updated = _resolve(case_id, resolution)
        if not updated:
            return jsonify({"error": f"Failure case not found: {case_id}"}), 404
        return jsonify({"resolved": True, "case_id": case_id})
    except Exception:
        logger.exception("Failed to resolve failure case %s", case_id)
        return jsonify({"error": "Failed to resolve failure case"}), 500


@teamwork_routes.route("/teamwork/eval/run", methods=["POST"])
def run_eval():
    """Run eval on a single failure case or full suite.

    JSON body: {case_id?: str, judge_tier?: str, replay?: bool, max_cases?: int}
    If case_id is provided, runs single case. Otherwise runs the full suite.
    """
    try:
        data = request.get_json(silent=True) or {}
        case_id = data.get("case_id")
        judge_tier = data.get("judge_tier", "low")
        replay = data.get("replay", True)

        if case_id:
            from prax.eval.runner import run_eval as _run_eval
            result = _run_eval(
                case_id=case_id,
                replay=replay,
                judge_tier=judge_tier,
            )
            return jsonify({
                "id": result.id,
                "case_id": result.case_id,
                "passed": result.passed,
                "score": result.score,
                "reasoning": result.reasoning,
                "judge_model": result.judge_model,
                "created_at": result.created_at,
            })
        else:
            from prax.eval.runner import run_eval_suite

            user_id = _get_teamwork_user_id()
            max_cases = data.get("max_cases", 20)
            report = run_eval_suite(
                user_id=user_id,
                replay=replay,
                judge_tier=judge_tier,
                max_cases=max_cases,
            )
            return jsonify(report)
    except Exception:
        logger.exception("Eval run failed")
        return jsonify({"error": "Eval run failed"}), 500


@teamwork_routes.route("/teamwork/eval/results", methods=["GET"])
def eval_results():
    """Return recent eval results.

    Query params: date=YYYY-MM-DD, limit=100
    """
    try:
        from prax.eval.runner import load_results

        date = request.args.get("date")
        limit = int(request.args.get("limit", "100"))
        results = load_results(date=date, limit=limit)
        return jsonify({
            "results": [
                {
                    "id": r.id,
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "score": r.score,
                    "reasoning": r.reasoning,
                    "judge_model": r.judge_model,
                    "created_at": r.created_at,
                }
                for r in results
            ],
        })
    except Exception:
        logger.exception("Failed to load eval results")
        return jsonify({"error": "Failed to load eval results"}), 500


# ---------------------------------------------------------------------------
# Memory API — STM, LTM, Graph, and stats
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/memory/config", methods=["GET"])
def memory_config():
    """Return whether the memory subsystem is enabled and the current user_id."""
    try:
        svc = _memory_service()
        user_id = _get_teamwork_user_id()
        return jsonify({
            "enabled": svc.available,
            "memory_enabled": svc.available,
            "user_id": user_id,
        })
    except Exception:
        logger.exception("Failed to get memory config")
        return jsonify({"error": "Failed to get memory config"}), 500


@teamwork_routes.route("/teamwork/memory/stm/<user_id>", methods=["GET"])
def memory_stm_list(user_id: str):
    """Return all STM (scratchpad) entries for a user."""
    try:
        from prax.services.memory.stm import stm_read
        entries = stm_read(user_id)
        return jsonify({
            "user_id": user_id,
            "entries": [
                {
                    "key": e.key,
                    "content": e.content,
                    "tags": e.tags,
                    "created_at": e.created_at,
                    "access_count": e.access_count,
                    "importance": e.importance,
                }
                for e in entries
            ],
        })
    except Exception:
        logger.exception("Failed to list STM entries for %s", user_id)
        return jsonify({"error": "Failed to list STM entries"}), 500


@teamwork_routes.route("/teamwork/memory/stm/<user_id>", methods=["PUT"])
def memory_stm_upsert(user_id: str):
    """Create or update an STM entry.

    JSON body: {key, content, tags?, importance?}
    """
    try:
        data = request.get_json(silent=True) or {}
        key = data.get("key", "").strip()
        content = data.get("content", "").strip()
        if not key or not content:
            return jsonify({"error": "key and content are required"}), 400

        from prax.services.memory.stm import stm_write
        entry = stm_write(
            user_id,
            key=key,
            content=content,
            tags=data.get("tags"),
            importance=data.get("importance", 0.5),
        )
        return jsonify({
            "key": entry.key,
            "content": entry.content,
            "tags": entry.tags,
            "created_at": entry.created_at,
            "access_count": entry.access_count,
            "importance": entry.importance,
        })
    except Exception:
        logger.exception("Failed to upsert STM entry for %s", user_id)
        return jsonify({"error": "Failed to upsert STM entry"}), 500


@teamwork_routes.route("/teamwork/memory/stm/<user_id>/<key>", methods=["DELETE"])
def memory_stm_delete(user_id: str, key: str):
    """Delete an STM entry by key."""
    try:
        from prax.services.memory.stm import stm_delete
        deleted = stm_delete(user_id, key)
        if not deleted:
            return jsonify({"error": f"STM entry not found: {key}"}), 404
        return jsonify({"deleted": True, "key": key})
    except Exception:
        logger.exception("Failed to delete STM entry '%s' for %s", key, user_id)
        return jsonify({"error": "Failed to delete STM entry"}), 500


@teamwork_routes.route("/teamwork/memory/ltm/<user_id>", methods=["GET"])
def memory_ltm_recall(user_id: str):
    """Recall long-term memories for a user.

    Query params: q=<search term>, top_k=5
    """
    try:
        svc = _memory_service()
        if not svc.available:
            return _memory_disabled_response()

        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"error": "q query parameter is required"}), 400

        top_k = int(request.args.get("top_k", "5"))
        memories = svc.recall(user_id, query, top_k=top_k)
        return jsonify({
            "user_id": user_id,
            "query": query,
            "memories": [
                {
                    "memory_id": m.memory_id,
                    "content": m.content,
                    "score": m.score,
                    "source": m.source,
                    "importance": m.importance,
                    "created_at": m.created_at,
                    "entities": m.entities,
                }
                for m in memories
            ],
        })
    except Exception:
        logger.exception("Failed to recall LTM for %s", user_id)
        return jsonify({"error": "Failed to recall memories"}), 500


@teamwork_routes.route("/teamwork/memory/ltm/<user_id>", methods=["POST"])
def memory_ltm_store(user_id: str):
    """Store a new long-term memory.

    JSON body: {content, importance?, tags?, source?}
    """
    try:
        svc = _memory_service()
        if not svc.available:
            return _memory_disabled_response()

        data = request.get_json(silent=True) or {}
        content = data.get("content", "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400

        memory_id = svc.remember(
            user_id,
            content=content,
            importance=data.get("importance", 0.5),
            tags=data.get("tags"),
            source=data.get("source", "api"),
        )
        if not memory_id:
            return jsonify({"error": "Failed to store memory"}), 500
        return jsonify({"memory_id": memory_id}), 201
    except Exception:
        logger.exception("Failed to store LTM for %s", user_id)
        return jsonify({"error": "Failed to store memory"}), 500


@teamwork_routes.route("/teamwork/memory/ltm/<user_id>/<memory_id>", methods=["DELETE"])
def memory_ltm_forget(user_id: str, memory_id: str):
    """Delete a specific long-term memory."""
    try:
        svc = _memory_service()
        if not svc.available:
            return _memory_disabled_response()

        deleted = svc.forget(user_id, memory_id)
        if not deleted:
            return jsonify({"error": f"Memory not found: {memory_id}"}), 404
        return jsonify({"deleted": True, "memory_id": memory_id})
    except Exception:
        logger.exception("Failed to forget memory '%s' for %s", memory_id, user_id)
        return jsonify({"error": "Failed to forget memory"}), 500


@teamwork_routes.route("/teamwork/memory/graph/<user_id>", methods=["GET"])
def memory_graph_stats(user_id: str):
    """Return graph stats and entity list for a user."""
    try:
        svc = _memory_service()
        if not svc.available:
            return _memory_disabled_response()

        from prax.services.memory.graph_store import get_stats, search_entities
        stats = get_stats(user_id)
        # Return top entities by mention count
        entities = search_entities(user_id, "", limit=50)
        return jsonify({
            "user_id": user_id,
            "stats": stats,
            "entities": entities,
        })
    except Exception:
        logger.exception("Failed to get graph stats for %s", user_id)
        return jsonify({"error": "Failed to get graph stats"}), 500


@teamwork_routes.route("/teamwork/memory/graph/<user_id>/entity/<name>", methods=["GET"])
def memory_graph_entity(user_id: str, name: str):
    """Return entity details with relations."""
    try:
        svc = _memory_service()
        if not svc.available:
            return _memory_disabled_response()

        entity = svc.entity_lookup(user_id, name)
        if not entity:
            return jsonify({"error": f"Entity not found: {name}"}), 404
        return jsonify({
            "id": entity.id,
            "name": entity.name,
            "display_name": entity.display_name,
            "entity_type": entity.entity_type,
            "importance": entity.importance,
            "mention_count": entity.mention_count,
            "first_seen": entity.first_seen,
            "last_seen": entity.last_seen,
            "properties": entity.properties,
            "relations": entity.relations,
        })
    except Exception:
        logger.exception("Failed to get entity '%s' for %s", name, user_id)
        return jsonify({"error": "Failed to get entity"}), 500


@teamwork_routes.route("/teamwork/memory/stats/<user_id>", methods=["GET"])
def memory_stats(user_id: str):
    """Return full memory system stats for a user."""
    try:
        svc = _memory_service()
        stats = svc.stats(user_id)
        return jsonify({"user_id": user_id, **stats})
    except Exception:
        logger.exception("Failed to get memory stats for %s", user_id)
        return jsonify({"error": "Failed to get memory stats"}), 500


# ---------------------------------------------------------------------------
# Claude Code Bridge — session visibility and management
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/claude-code/sessions", methods=["GET"])
def claude_code_sessions():
    """List active Claude Code sessions.

    Returns session IDs, turn counts, and idle times so the user
    can see if a session is dangling.
    """
    try:
        from prax.agent.claude_code_tools import (
            _bridge_url,
            is_bridge_available,
        )
        from prax.agent.claude_code_tools import (
            _headers as _bridge_headers,
        )

        if not is_bridge_available():
            return jsonify({"sessions": [], "bridge_available": False})

        import requests as _req
        resp = _req.get(
            f"{_bridge_url()}/sessions",
            headers=_bridge_headers(),
            timeout=5,
        )
        if not resp.ok:
            return jsonify({"sessions": [], "error": "Bridge returned error"}), 502

        sessions = resp.json().get("sessions", [])
        return jsonify({"sessions": sessions, "bridge_available": True})
    except Exception:
        logger.exception("Failed to list Claude Code sessions")
        return jsonify({"error": "Failed to query bridge"}), 500


@teamwork_routes.route("/teamwork/claude-code/sessions/<session_id>", methods=["DELETE"])
def claude_code_kill_session(session_id: str):
    """Terminate a Claude Code session.

    Allows the user to kill a dangling session from the UI.
    """
    try:
        from prax.agent.claude_code_tools import _post, is_bridge_available
        from prax.services.teamwork_hooks import mirror_coding_agent_turn

        if not is_bridge_available():
            return jsonify({"error": "Bridge not available"}), 503

        result = _post("/session/end", {"session_id": session_id})
        if "error" in result:
            return jsonify({"error": result["error"]}), 400

        turns = result.get("turns", 0)
        mirror_coding_agent_turn(
            "claude-code",
            prax_message=None,
            agent_response=None,
            meta=f"Session `{session_id}` terminated by user after {turns} turns.",
        )
        return jsonify({"ended": True, "session_id": session_id, "turns": turns})
    except Exception:
        logger.exception("Failed to terminate Claude Code session")
        return jsonify({"error": "Failed to terminate session"}), 500


# ---------------------------------------------------------------------------
# Scheduler — cron job and reminder management
# ---------------------------------------------------------------------------

def _scheduler_user_id() -> str:
    """Return the user ID for scheduler operations."""
    return _get_teamwork_user_id()


@teamwork_routes.route("/teamwork/schedules", methods=["GET"])
def list_schedules():
    """List all cron schedules and one-time reminders for the current user."""
    try:
        from prax.services import scheduler_service
        uid = _scheduler_user_id()
        schedules = scheduler_service.list_schedules(uid)
        reminders = scheduler_service.list_reminders(uid)
        return jsonify({"schedules": schedules, "reminders": reminders})
    except Exception:
        logger.exception("Failed to list schedules")
        return jsonify({"error": "Failed to list schedules"}), 500


@teamwork_routes.route("/teamwork/schedules", methods=["POST"])
def create_schedule():
    """Create a new cron schedule."""
    try:
        from prax.services import scheduler_service
        data = request.get_json(silent=True) or {}
        uid = _scheduler_user_id()
        result = scheduler_service.create_schedule(
            uid,
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            cron_expr=data.get("cron", ""),
            timezone=data.get("timezone"),
            channel=data.get("channel"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to create schedule")
        return jsonify({"error": "Failed to create schedule"}), 500


@teamwork_routes.route("/teamwork/schedules/<schedule_id>", methods=["PATCH"])
def update_schedule(schedule_id: str):
    """Update a schedule (description, prompt, cron, timezone, enabled)."""
    try:
        from prax.services import scheduler_service
        data = request.get_json(silent=True) or {}
        uid = _scheduler_user_id()
        result = scheduler_service.update_schedule(
            uid, schedule_id,
            description=data.get("description"),
            prompt=data.get("prompt"),
            cron=data.get("cron"),
            timezone=data.get("timezone"),
            enabled=data.get("enabled"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to update schedule")
        return jsonify({"error": "Failed to update schedule"}), 500


@teamwork_routes.route("/teamwork/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id: str):
    """Delete a schedule permanently."""
    try:
        from prax.services import scheduler_service
        uid = _scheduler_user_id()
        result = scheduler_service.delete_schedule(uid, schedule_id)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete schedule")
        return jsonify({"error": "Failed to delete schedule"}), 500


@teamwork_routes.route("/teamwork/reminders", methods=["POST"])
def create_reminder():
    """Create a one-time reminder."""
    try:
        from prax.services import scheduler_service
        data = request.get_json(silent=True) or {}
        uid = _scheduler_user_id()
        result = scheduler_service.create_reminder(
            uid,
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            fire_at=data.get("fire_at", ""),
            timezone=data.get("timezone"),
            channel=data.get("channel"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result), 201
    except Exception:
        logger.exception("Failed to create reminder")
        return jsonify({"error": "Failed to create reminder"}), 500


@teamwork_routes.route("/teamwork/reminders/<reminder_id>", methods=["PATCH"])
def update_reminder(reminder_id: str):
    """Update a pending reminder (description, prompt, fire_at, timezone, channel)."""
    try:
        from prax.services import scheduler_service
        data = request.get_json(silent=True) or {}
        uid = _scheduler_user_id()
        result = scheduler_service.update_reminder(
            uid, reminder_id,
            description=data.get("description"),
            prompt=data.get("prompt"),
            fire_at=data.get("fire_at"),
            timezone=data.get("timezone"),
            channel=data.get("channel"),
        )
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to update reminder")
        return jsonify({"error": "Failed to update reminder"}), 500


@teamwork_routes.route("/teamwork/reminders/<reminder_id>", methods=["DELETE"])
def delete_reminder(reminder_id: str):
    """Delete a pending reminder."""
    try:
        from prax.services import scheduler_service
        uid = _scheduler_user_id()
        result = scheduler_service.delete_reminder(uid, reminder_id)
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to delete reminder")
        return jsonify({"error": "Failed to delete reminder"}), 500


@teamwork_routes.route("/teamwork/timezone", methods=["GET"])
def get_timezone():
    """Get the user's default timezone."""
    try:
        from prax.services import scheduler_service
        uid = _scheduler_user_id()
        data = scheduler_service._read_schedules(uid)
        return jsonify({"timezone": data.get("timezone", "UTC")})
    except Exception:
        return jsonify({"timezone": "UTC"})


@teamwork_routes.route("/teamwork/timezone", methods=["PUT"])
def set_timezone():
    """Set the user's default timezone."""
    try:
        from prax.services import scheduler_service
        data = request.get_json(silent=True) or {}
        uid = _scheduler_user_id()
        result = scheduler_service.set_user_timezone(uid, data.get("timezone", "UTC"))
        if "error" in result:
            return jsonify({"error": result["error"]}), 400
        return jsonify(result)
    except Exception:
        logger.exception("Failed to set timezone")
        return jsonify({"error": "Failed to set timezone"}), 500


# ---------------------------------------------------------------------------
# Context Management — stats and manual compaction
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/context/stats", methods=["GET"])
def context_stats():
    """Return context window stats for the current user."""
    try:
        from prax.agent.context_manager import count_message_tokens, count_tokens, get_context_limit
        from prax.services.conversation_service import conversation_service

        uid = _get_teamwork_user_id()
        history = conversation_service._build_history(
            int(uid.replace("-", "")[:15], 16),
        )

        # Count tokens in current history
        from langchain_core.messages import AIMessage as AM
        from langchain_core.messages import HumanMessage as HM
        msgs = []
        for h in history:
            if h.get("role") == "user":
                msgs.append(HM(content=h.get("content", "")))
            elif h.get("role") == "assistant":
                msgs.append(AM(content=h.get("content", "")))

        history_tokens = count_message_tokens(msgs) if msgs else 0

        # System prompt size
        from prax.agent.orchestrator import _load_system_prompt
        sys_tokens = count_tokens(_load_system_prompt())

        # Current model info for context limit resolution
        from prax.agent.orchestrator import get_model_override
        from prax.plugins.llm_config import get_component_config as _gcc
        _cfg = _gcc("orchestrator")
        override = get_model_override()
        current_model = override or _cfg.get("model") or ""
        current_tier = _cfg.get("tier") or "low"
        if not current_model:
            from prax.agent.model_tiers import resolve_model as _rm
            current_model = _rm(current_tier)
        context_limit = get_context_limit(current_tier, current_model)

        return jsonify({
            "history_messages": len(history),
            "history_tokens": history_tokens,
            "system_prompt_tokens": sys_tokens,
            "total_tokens": history_tokens + sys_tokens,
            "context_limit": context_limit,
            "current_model": current_model,
            "current_tier": current_tier,
            "limits": {
                "low": get_context_limit("low"),
                "medium": get_context_limit("medium"),
                "high": get_context_limit("high"),
                "pro": get_context_limit("pro"),
            },
        })
    except Exception:
        logger.exception("Failed to get context stats")
        return jsonify({"error": "Failed to get context stats"}), 500


@teamwork_routes.route("/teamwork/context/compact", methods=["POST"])
def context_compact():
    """Trigger manual compaction of the conversation history.

    Currently performs a dry-run analysis showing how much space compaction
    would reclaim. Full persistence is not yet implemented because the
    conversation store doesn't expose a rewrite API.
    """
    try:
        from prax.services.conversation_service import conversation_service

        uid = _get_teamwork_user_id()
        conversation_key = int(uid.replace("-", "")[:15], 16)
        history = conversation_service._build_history(conversation_key)

        if not history:
            return jsonify({"compacted": False, "reason": "No history to compact"})

        from langchain_core.messages import AIMessage as AM
        from langchain_core.messages import HumanMessage as HM
        msgs = []
        for h in history:
            if h.get("role") == "user":
                msgs.append(HM(content=h.get("content", "")))
            elif h.get("role") == "assistant":
                msgs.append(AM(content=h.get("content", "")))

        from prax.agent.context_manager import compact_history, count_message_tokens
        before_tokens = count_message_tokens(msgs)
        compacted = compact_history(msgs)
        after_tokens = count_message_tokens(compacted)

        return jsonify({
            "compacted": True,
            "dry_run": True,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "messages_before": len(msgs),
            "messages_after": len(compacted),
            "savings_tokens": before_tokens - after_tokens,
        })
    except Exception:
        logger.exception("Failed to compact context")
        return jsonify({"error": "Failed to compact context"}), 500


# ---------------------------------------------------------------------------
# Model Picker API
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/model", methods=["GET"])
def get_model():
    """Return the current orchestrator model, tier, and available models."""
    try:
        from prax.agent.model_tiers import get_available_tiers
        from prax.agent.orchestrator import get_model_override
        from prax.plugins.llm_config import get_component_config

        cfg = get_component_config("orchestrator")
        override = get_model_override()
        current_tier = cfg.get("tier") or "low"

        # Determine current effective model
        if override:
            current_model = override
        elif cfg.get("model"):
            current_model = cfg.get("model")
        else:
            from prax.agent.model_tiers import resolve_model
            current_model = resolve_model(current_tier)

        # Build available models list
        available = []
        for tc in get_available_tiers():
            available.append({
                "tier": tc.tier.value,
                "model": tc.model,
            })

        return jsonify({
            "current_model": current_model,
            "current_tier": current_tier,
            "override": override,
            "available": available,
        })
    except Exception:
        logger.exception("Failed to get model info")
        return jsonify({"error": "Failed to get model info"}), 500


@teamwork_routes.route("/teamwork/model", methods=["PUT"])
def set_model():
    """Set a runtime model override for the orchestrator.

    JSON body: {"model": "claude-sonnet-4-6"} or {"model": "auto"} to clear.
    """
    try:
        data = request.get_json(silent=True) or {}
        model = data.get("model", "").strip()
        if not model:
            return jsonify({"error": "model is required"}), 400

        from prax.agent.orchestrator import get_model_override, set_model_override
        set_model_override(model)

        effective_override = get_model_override()
        return jsonify({
            "override": effective_override,
            "message": f"Model override set to {effective_override}" if effective_override else "Model override cleared (auto mode)",
        })
    except Exception:
        logger.exception("Failed to set model override")
        return jsonify({"error": "Failed to set model override"}), 500


# ---------------------------------------------------------------------------
# Health monitoring
# ---------------------------------------------------------------------------


@teamwork_routes.route("/teamwork/health", methods=["GET"])
def health_status():
    """Return health monitoring data for the TeamWork UI."""
    try:
        from prax.agent.health_monitor import get_health_status
        return jsonify(get_health_status())
    except Exception:
        logger.exception("Failed to get health status")
        return jsonify({"error": "Failed to get health status"}), 500


@teamwork_routes.route("/teamwork/health/events", methods=["GET"])
def health_events():
    """Return recent health events for the TeamWork UI."""
    try:
        minutes = request.args.get("minutes", "60", type=int)
        category = request.args.get("category")
        severity = request.args.get("severity")
        limit = request.args.get("limit", "100", type=int)
        from prax.services.health_telemetry import get_recent_events
        events = get_recent_events(
            minutes=minutes, category=category,
            severity=severity, limit=limit,
        )
        return jsonify({"events": events})
    except Exception:
        logger.exception("Failed to get health events")
        return jsonify({"error": "Failed to get health events"}), 500


# ---------------------------------------------------------------------------
# Pipeline coverage (Phase 0 of pipeline evolution roadmap)
# ---------------------------------------------------------------------------


@teamwork_routes.route("/teamwork/pipeline-coverage", methods=["GET"])
def pipeline_coverage_report():
    """Return the Pareto coverage report.

    Query params:
      - days (int, default 14): look-back window in days
      - top_n (int, default 20): top N clusters to include

    See docs/PIPELINE_EVOLUTION_TODO.md for how to interpret the results.
    """
    try:
        days = request.args.get("days", "14", type=int)
        top_n = request.args.get("top_n", "20", type=int)
        from prax.services.pipeline_coverage import get_coverage_report
        report = get_coverage_report(days=days, top_n=top_n)
        return jsonify(report)
    except Exception:
        logger.exception("Failed to get pipeline coverage report")
        return jsonify({"error": "Failed to get pipeline coverage report"}), 500


@teamwork_routes.route("/teamwork/pipeline-coverage/events", methods=["GET"])
def pipeline_coverage_events():
    """Return raw recent coverage events for inspection (no embeddings)."""
    try:
        days = request.args.get("days", "14", type=int)
        limit = request.args.get("limit", "200", type=int)
        from prax.services.pipeline_coverage import get_recent_events
        events = get_recent_events(days=days, limit=limit)
        # Strip embeddings — they're large and not useful in the JSON response.
        for evt in events:
            evt.pop("embedding", None)
        return jsonify({"events": events})
    except Exception:
        logger.exception("Failed to get pipeline coverage events")
        return jsonify({"error": "Failed to get pipeline coverage events"}), 500


@teamwork_routes.route("/teamwork/pipeline-coverage/test-mode", methods=["POST", "GET"])
def pipeline_coverage_test_mode():
    """Toggle pipeline-coverage test mode without restarting the app.

    POST body:
        {"enabled": true|false, "file": "/optional/path/to/file.jsonl"}

    When enabled, all coverage events are written to a separate JSONL
    file (default ``<workspace>/.pipeline_coverage_harness.jsonl``) so
    harness scenarios never mix with real user data. The in-memory
    event buffer is cleared on every toggle.

    GET returns the current state (no body required).
    """
    try:
        from pathlib import Path as _Path

        from prax.services import pipeline_coverage as _pc
        from prax.services.pipeline_coverage import (
            is_test_mode,
            set_test_mode,
        )

        if request.method == "GET":
            return jsonify({
                "enabled": is_test_mode(),
                "file": str(_pc._test_file_path) if _pc._test_file_path else None,
            })

        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        raw_path = body.get("file")
        test_file = _Path(raw_path) if raw_path else None
        set_test_mode(enabled, test_file=test_file)
        return jsonify({
            "enabled": is_test_mode(),
            "file": str(_pc._test_file_path) if _pc._test_file_path else None,
        })
    except Exception:
        logger.exception("Failed to toggle pipeline coverage test mode")
        return jsonify({"error": "Failed to toggle pipeline coverage test mode"}), 500
