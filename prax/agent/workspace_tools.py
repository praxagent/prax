"""LangChain tool wrappers for the git-backed workspace."""
from __future__ import annotations

import logging as _logging

from langchain_core.tools import tool

from prax.agent.action_policy import RiskLevel, risk_tool
from prax.agent.user_context import current_user_id
from prax.services import workspace_service
from prax.trace_events import TraceEvent

_ws_logger = _logging.getLogger(__name__)


def _get_user_id() -> str:
    uid = current_user_id.get()
    if not uid:
        return "unknown"
    return uid


def _auto_advance_plan_step() -> None:
    """Mark the next incomplete plan step as done after a workspace write.

    Called automatically by workspace_save so the plan enforcement loop
    doesn't re-trigger work that's already finished.  Sequential: marks
    the first incomplete step as done.
    """
    try:
        uid = _get_user_id()
        if uid == "unknown":
            return
        plan = workspace_service.read_plan(uid)
        if not plan:
            return
        for step in plan.get("steps", []):
            if not step.get("done"):
                workspace_service.complete_plan_step(uid, step["step"])
                _ws_logger.info(
                    "Auto-advanced plan step %d after workspace_save: %s",
                    step["step"], step.get("description", "")[:60],
                )
                return
    except Exception:
        pass


@tool
def user_notes_update(content: str) -> str:
    """Update user_notes.md with things to remember about this user.

    Write the FULL content of the notes file each time (not just new lines).
    Include: timezone, name, preferences, interests, or anything they ask you to remember.
    These notes are automatically loaded into your context on every conversation.
    """
    try:
        workspace_service.save_user_notes(_get_user_id(), content)
        return "User notes updated."
    except Exception as e:
        return f"Failed to update user notes: {e}"


@tool
def user_notes_read() -> str:
    """Read the current user notes to recall what you know about this user."""
    try:
        notes = workspace_service.read_user_notes(_get_user_id())
        if not notes:
            return "No user notes yet. Use user_notes_update to start keeping notes."
        return f"User notes:\n{notes}"
    except Exception as e:
        return f"Failed to read user notes: {e}"


@tool
def workspace_save(filename: str, content: str) -> str:
    """Save a file to the active workspace. Use for markdown notes, extracted content, etc."""
    try:
        uid = _get_user_id()
        workspace_service.save_file(uid, filename, content)

        # Self-verification: confirm the file was actually saved
        verify_msg = ""
        try:
            from prax.agent.verification import verify_workspace_file
            ws_root = workspace_service.workspace_root(uid)
            result = verify_workspace_file(ws_root, filename)
            if not result.passed:
                verify_msg = f" [WARNING: verification failed: {result.summary}]"
                _ws_logger.warning("workspace_save verification failed for %s: %s", filename, result.summary)
        except Exception:
            pass

        # Auto-advance the plan — saving a file is a concrete step completion.
        _auto_advance_plan_step()
        return f"Saved {filename} to active workspace.{verify_msg}"
    except Exception as e:
        return f"Failed to save {filename}: {e}"


@tool
def workspace_patch(filename: str, old_text: str, new_text: str) -> str:
    """Apply a precise text replacement to a workspace file.

    Instead of rewriting the entire file, find old_text and replace it with
    new_text.  Fails if old_text is not found or appears more than once.

    Args:
        filename: The file to patch in the active workspace.
        old_text: The exact text to find (must appear exactly once).
        new_text: The replacement text.
    """
    try:
        content = workspace_service.read_file(_get_user_id(), filename)
        count = content.count(old_text)
        if count == 0:
            return f"old_text not found in {filename}. Read the file first to get the exact text."
        if count > 1:
            return f"old_text appears {count} times in {filename}. Provide more context to make it unique."
        patched = content.replace(old_text, new_text, 1)
        workspace_service.save_file(_get_user_id(), filename, patched)
        return f"Patched {filename}: replaced {len(old_text)} chars with {len(new_text)} chars."
    except FileNotFoundError:
        return f"File {filename} not found in active workspace."
    except Exception as e:
        return f"Failed to patch {filename}: {e}"


@tool
def workspace_read(filename: str) -> str:
    """Read a file from the active workspace to recall its content."""
    try:
        return workspace_service.read_file(_get_user_id(), filename)
    except FileNotFoundError:
        return f"File {filename} not found in active workspace."
    except Exception as e:
        return f"Failed to read {filename}: {e}"


@tool
def workspace_list() -> str:
    """List all files currently in the active workspace."""
    files = workspace_service.list_active(_get_user_id())
    if not files:
        return "Active workspace is empty."
    return "Active workspace files:\n" + "\n".join(f"  - {f}" for f in files)


@tool
def latex_compile(filename: str) -> str:
    """Compile a .tex file in the active workspace to PDF using pdflatex.

    Runs pdflatex twice locally (for references/TOC). Much faster than the sandbox.
    The resulting PDF will be in the active workspace with the same base name.
    """
    import os
    import subprocess

    uid = _get_user_id()
    root = workspace_service._workspace_root(uid)
    active = os.path.join(root, "active")
    tex_path = os.path.join(active, filename)

    if not os.path.isfile(tex_path):
        return f"File {filename} not found in active workspace."
    if not filename.endswith(".tex"):
        return f"{filename} is not a .tex file."

    try:
        for _pass_num in (1, 2):
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", f"-output-directory={active}", tex_path],
                capture_output=True, text=True, timeout=60, cwd=active,
            )
        pdf_name = filename.rsplit(".", 1)[0] + ".pdf"
        pdf_path = os.path.join(active, pdf_name)
        if os.path.isfile(pdf_path):
            size_kb = os.path.getsize(pdf_path) // 1024
            return f"Compiled {pdf_name} ({size_kb} KB). Use workspace_send_file to deliver it."
        else:
            errors = result.stdout[-500:] if result.stdout else result.stderr[-500:]
            return f"pdflatex failed to produce a PDF. Last output:\n{errors}"
    except subprocess.TimeoutExpired:
        return "pdflatex timed out (60s limit)."
    except FileNotFoundError:
        return "pdflatex is not installed on this system."


@risk_tool(risk=RiskLevel.MEDIUM)
def workspace_send_file(filename: str, message: str = "") -> str:
    """Send a file from the active workspace to the user via their current channel.

    Delivery is attempted in order:
    1. Discord direct upload (files under ~8 MB)
    2. TeamWork message with workspace path (if the user is on TeamWork)
    3. ngrok share link (public URL, works for any channel)
    4. Workspace fallback — tells the user where the file is stored

    Use this to deliver PDFs, images, videos, or other files the user asked for.
    """
    import os

    uid = _get_user_id()
    root = workspace_service._workspace_root(uid)
    file_path = os.path.join(root, "active", filename)
    if not os.path.isfile(file_path):
        return f"File {filename} not found in active workspace."

    size_mb = os.path.getsize(file_path) / (1024 * 1024)

    # Try Discord direct upload for small files.
    if size_mb < 8:
        try:
            from prax.services.discord_service import send_file
            send_file(uid, file_path, message=message)
            return f"Sent {filename} to the user via Discord ({size_mb:.1f} MB)."
        except RuntimeError:
            pass  # Discord not running — fall through.
        except Exception:
            pass

    # Try TeamWork — if the user is on a TeamWork channel, post a message
    # with the filename.  TeamWork's file browser has access to the workspace.
    teamwork_delivered = _deliver_via_teamwork(uid, filename, size_mb, message)
    if teamwork_delivered:
        return teamwork_delivered

    # Fall back to ngrok share link.
    try:
        result = workspace_service.publish_file(uid, f"active/{filename}")
        if "error" not in result:
            url = result["url"]
            # Try to send the link via the user's channel.
            _deliver_share_link(uid, url, filename, message)
            return (
                f"Shared {filename} via link ({size_mb:.1f} MB): {url}\n"
                f"Token: `{result['token']}` — use workspace_unshare_file to revoke."
            )
    except Exception:
        pass

    # Final fallback — file is in the workspace, tell the user where.
    return (
        f"File saved to your workspace: **{filename}** ({size_mb:.1f} MB).\n"
        f"You can access it from the TeamWork file browser or your workspace directory."
    )


def _deliver_via_teamwork(
    user_id: str, filename: str, size_mb: float, message: str,
) -> str | None:
    """Try to deliver a file notification via TeamWork.

    TeamWork's file browser has direct access to the workspace, so we post
    a message with attachment metadata.  For media files (audio/video) the
    chat UI renders an inline player; other files get a download button.
    Returns the success string, or None if TeamWork is not the active channel.
    """
    try:
        import mimetypes
        import uuid

        from prax.agent.user_context import current_channel_id
        from prax.services.teamwork_service import get_teamwork_client

        channel_id = current_channel_id.get(None)
        if not channel_id:
            return None

        tw = get_teamwork_client()
        if not tw.enabled:
            return None

        project_id = tw.project_id
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"
        download_url = f"/api/workspace/{project_id}/download?path=active/{filename}"

        note = message or "Your file is ready"
        tw.send_message(
            content=f"{note}",
            channel_id=channel_id,
            agent_name="Prax",
            extra_data={
                "attachments": [{
                    "id": uuid.uuid4().hex[:12],
                    "name": filename,
                    "url": download_url,
                    "content_type": content_type,
                    "size": int(size_mb * 1024 * 1024),
                }],
            },
        )
        return (
            f"Delivered {filename} ({size_mb:.1f} MB) via TeamWork. "
            f"The user can play or download it from the chat."
        )
    except Exception:
        return None


def _deliver_share_link(user_id: str, url: str, filename: str, message: str) -> None:
    """Best-effort: send the share link to the user via their active channel."""
    text = f"{message}\n{url}" if message else f"Here's your file ({filename}): {url}"

    # Try TeamWork first (if active channel).
    try:
        from prax.agent.user_context import current_channel_id
        from prax.services.teamwork_service import get_teamwork_client

        channel_id = current_channel_id.get(None)
        if channel_id:
            tw = get_teamwork_client()
            if tw.enabled:
                tw.send_message(
                    content=text, channel_id=channel_id, agent_name="Prax",
                )
                return
    except Exception:
        pass

    # Try Discord text message.
    try:
        from prax.services.discord_service import send_message
        send_message(user_id, text)
        return
    except Exception:
        pass

    # Try SMS.
    try:
        from prax.sms import send_sms
        # SMS users have phone-number user IDs (start with + or digit).
        if user_id and user_id.lstrip("+").isdigit():
            send_sms(text, user_id)
    except Exception:
        pass


@tool
def workspace_archive(filename: str) -> str:
    """Move a file from active workspace to archive. Use when done discussing a document."""
    try:
        workspace_service.archive_file(_get_user_id(), filename)
        return f"Archived {filename}. It can be restored later with workspace_restore."
    except FileNotFoundError:
        return f"File {filename} not found in active workspace."
    except Exception as e:
        return f"Failed to archive {filename}: {e}"


@tool
def workspace_search(query: str) -> str:
    """Search the archive for files matching a keyword. Returns filenames and snippets."""
    results = workspace_service.search_archive(_get_user_id(), query)
    if not results:
        return f"No archived files match '{query}'."
    lines = []
    for r in results:
        lines.append(f"**{r['filename']}**:\n{r['snippet']}")
    return "\n\n".join(lines)


@tool
def workspace_restore(filename: str) -> str:
    """Restore a file from the archive back to the active workspace for discussion."""
    try:
        workspace_service.restore_file(_get_user_id(), filename)
        return f"Restored {filename} to active workspace."
    except FileNotFoundError:
        return f"File {filename} not found in archive."
    except Exception as e:
        return f"Failed to restore {filename}: {e}"


@tool
def log_link(url: str, description: str = "") -> str:
    """Log a URL the user shared to the running links history.

    Call this EVERY TIME the user shares a URL in conversation.  This builds
    a profile of the user's interests over time so you can make better
    suggestions later.  Include a short description of what the link is about
    if you know.
    """
    try:
        workspace_service.append_link(_get_user_id(), url, description)
        return f"Link logged: {url}"
    except Exception as e:
        return f"Failed to log link: {e}"


@tool
def links_history() -> str:
    """Read the user's full link history — every URL they have ever shared."""
    try:
        links = workspace_service.read_links(_get_user_id())
        if not links:
            return "No links logged yet."
        return links
    except Exception as e:
        return f"Failed to read links: {e}"


@tool
def reread_instructions() -> str:
    """Re-read your system instructions from the workspace reference file.

    Call this when you feel confused about your capabilities, available tools,
    or how you should behave — especially during long conversations where your
    original instructions may have been compressed out of context.
    """
    text = workspace_service.read_instructions(_get_user_id())
    if not text:
        return "No instructions file found in workspace."
    return text


@tool
def todo_add(task: str) -> str:
    """Add an item to the user's personal to-do list.

    Call this when the user says things like "add X to my to-do list",
    "remind me to X" (if it's a task, not a timed reminder), or "I need to X".
    """
    entry = workspace_service.add_todo(_get_user_id(), task)
    return f"Added #{entry['id']}: {entry['task']}"


@tool
def todo_list(show_completed: bool = False) -> str:
    """Show the user's to-do list.

    By default shows only incomplete items.  Set show_completed=True to see
    everything including done items.
    """
    todos = workspace_service.list_todos(_get_user_id(), show_completed=show_completed)
    if not todos:
        return "Your to-do list is empty!" if not show_completed else "No to-do items at all."
    lines = []
    for t in todos:
        check = "x" if t.get("done") else " "
        lines.append(f"  {t['id']}. [{check}] {t['task']}")
    return "Your to-do list:\n" + "\n".join(lines)


@tool
def todo_complete(item_ids: list[int]) -> str:
    """Mark to-do items as completed.  Pass one or more item numbers.

    Example: the user says "done with 3 and 5" → call todo_complete([3, 5]).
    """
    result = workspace_service.complete_todo(_get_user_id(), item_ids)
    if "error" in result:
        return result["error"]
    return f"Marked #{', #'.join(str(i) for i in result['ids'])} as done!"


@tool
def todo_remove(item_ids: list[int]) -> str:
    """Remove items from the to-do list entirely (drop, not complete).

    Example: the user says "drop 3, 5, and 10" → call todo_remove([3, 5, 10]).
    Numbers will be re-assigned after removal.
    """
    result = workspace_service.remove_todos(_get_user_id(), item_ids)
    if "error" in result:
        return result["error"]
    return f"Removed. {result['remaining']} items remaining."


@tool
def agent_plan(goal: str, steps: list[str]) -> str:
    """Break a complex request into a numbered plan of steps.

    Use this when a user's request requires multiple tool calls or sequential
    actions.  Write out the steps FIRST, then work through them one by one,
    calling agent_step_done after each.

    Args:
        goal: One-line summary of what the user wants.
        steps: Ordered list of discrete actions to take.
    """
    user_id = _get_user_id()
    plan = workspace_service.create_plan(user_id, goal, steps)
    lines = [f"Plan created: {plan['goal']}"]
    for s in plan["steps"]:
        lines.append(f"  {s['step']}. [ ] {s['description']}")

    # Mirror plan to TeamWork: announce in #general and create board task.
    from prax.services.task_board import create_plan_task
    from prax.services.teamwork_hooks import post_to_channel, set_role_status
    set_role_status("Planner", "idle")
    post_to_channel("general", "\n".join(lines), agent_name="Planner")
    create_plan_task(user_id, plan["id"], goal, plan["steps"])

    return "\n".join(lines)


@tool
def agent_step_done(step: int) -> str:
    """Mark a plan step as completed after you finish it."""
    user_id = _get_user_id()
    result = workspace_service.complete_plan_step(user_id, step)
    if "error" in result:
        return result["error"]
    s = result["step"]

    # Sync progress to TeamWork board task.
    plan = workspace_service.read_plan(user_id)
    if plan:
        from prax.services.task_board import update_plan_task_progress
        update_plan_task_progress(plan["id"], plan["steps"])

    return f"Step {s['step']} done: {s['description']}"


@tool
def agent_plan_status() -> str:
    """Show the current plan and which steps are done."""
    plan = workspace_service.read_plan(_get_user_id())
    if not plan:
        return "No active plan."
    lines = [f"Goal: {plan['goal']}"]
    for s in plan["steps"]:
        check = "x" if s["done"] else " "
        lines.append(f"  {s['step']}. [{check}] {s['description']}")
    done = sum(1 for s in plan["steps"] if s["done"])
    lines.append(f"\nProgress: {done}/{len(plan['steps'])}")
    return "\n".join(lines)


@tool
def agent_plan_clear() -> str:
    """Clear the plan when all steps are complete or the task is abandoned."""
    user_id = _get_user_id()

    # Mark the board task as completed before clearing the plan file.
    plan = workspace_service.read_plan(user_id)
    if plan:
        from prax.services.task_board import complete_plan_task
        complete_plan_task(plan["id"])

    workspace_service.clear_plan(user_id)
    return "Plan cleared."


@tool
def read_logs(lines: int = 150, level: str = "") -> str:
    """Read recent application logs.

    Use this to investigate errors, warnings, or unexpected behavior you or
    the user noticed.  Returns the most recent log lines.

    Args:
        lines: Number of recent lines to return (default 150, max 500).
        level: Optional filter — "ERROR", "WARNING", "INFO".  Empty = all levels.
    """
    import os

    from prax.settings import settings as _settings

    lines = min(max(lines, 10), 500)
    # Use the configured log path directly — it's where FileHandler writes.
    log_path = _settings.log_path

    if not os.path.isfile(log_path):
        return f"Log file not found at {log_path}."

    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return f"Failed to read logs: {e}"

    # Take the tail.
    recent = all_lines[-lines:]

    # Filter by level if requested.
    if level:
        level_upper = level.upper()
        recent = [ln for ln in recent if f"[{level_upper}]" in ln]

    if not recent:
        return f"No log lines found (filter: {level or 'none'})."

    return f"Last {len(recent)} log lines:\n" + "".join(recent)


@tool
def system_status() -> str:
    """Show system health: loaded plugins, tool count, recent errors, and config.

    Use this to diagnose issues, check what's available, or verify that
    a plugin/tool is loaded after changes.
    """
    lines = []
    try:
        from prax.plugins.loader import get_plugin_loader
        from prax.settings import settings as _settings

        loader = get_plugin_loader()
        plugin_tools = loader.get_tools()
        plugin_names = sorted({t.name.split("_")[0] for t in plugin_tools}) if plugin_tools else []

        from prax.agent.tool_registry import get_registered_tools
        total_tools = len(get_registered_tools())

        lines.append(f"**Tools:** {total_tools} total")
        lines.append(f"**Plugins:** {len(plugin_names)} loaded ({', '.join(plugin_names)})")
        lines.append(f"**Plugin tools:** {len(plugin_tools)}")

        # Plugin health from monitored wrappers.
        registry = loader._registry if hasattr(loader, "_registry") else None
        if registry and hasattr(registry, "get_all_status"):
            statuses = registry.get_all_status()
            failing = {k: v for k, v in statuses.items() if v.get("failures", 0) > 0}
            if failing:
                lines.append("**Failing plugins:**")
                for name, info in failing.items():
                    lines.append(f"  - {name}: {info['failures']} failures")
            else:
                lines.append("**Plugin health:** all OK")

        lines.append(f"**LLM:** {_settings.default_llm_provider} / {_settings.base_model}")
        lines.append(f"**Self-improve:** {'enabled' if _settings.self_improve_enabled else 'disabled'}")
        lines.append(f"**Sandbox:** {'persistent' if _settings.sandbox_persistent else 'ephemeral'}")

        # Recent errors from app log.
        log_path = _settings.log_path
        import os
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                log_lines = f.readlines()
            errors = [ln.strip() for ln in log_lines[-500:] if "[ERROR]" in ln]
            if errors:
                lines.append(f"**Recent errors:** {len(errors)} in last 500 log lines")
                for e in errors[-3:]:
                    lines.append(f"  {e[:200]}")
            else:
                lines.append("**Recent errors:** none")
    except Exception as e:
        lines.append(f"Error gathering status: {e}")
    return "\n".join(lines)


@tool
def conversation_history(lines: int = 200) -> str:
    """Read recent conversation history from the trace log.

    Returns the most recent messages (user, assistant, tool calls) across
    all past conversations with this user.  Use this to recall what was
    discussed previously — topics, decisions, links shared, etc.

    Args:
        lines: Number of lines to return (default 200, max 1000).
    """
    try:
        lines = min(max(lines, 10), 1000)
        content = workspace_service.read_trace_tail(_get_user_id(), lines)
        if not content:
            return "No conversation history found."
        return content
    except Exception as e:
        return f"Error reading history: {e}"


@tool
def conversation_search(query: str, max_results: int = 20) -> str:
    """Search past conversations for a topic, keyword, or phrase.

    Searches across all past conversations with this user — messages,
    tool calls, and results.  Returns matching excerpts with timestamps.

    Use this when the user asks "did we talk about X?", "when did I mention Y?",
    or when you need to recall a prior discussion for context.

    Args:
        query: Search term or phrase to look for.
        max_results: Maximum number of matches to return (default 20).
    """
    try:
        results = workspace_service.search_trace(
            _get_user_id(), query, min(max(max_results, 1), 50)
        )
        if not results:
            return f"No matches for '{query}' in conversation history."
        lines = []
        for r in results:
            lines.append(f"**{r['timestamp']}**\n{r['excerpt']}")
        return f"Found {len(results)} match(es):\n\n" + "\n\n---\n\n".join(lines)
    except Exception as e:
        return f"Error searching history: {e}"


@tool
def think(reasoning: str) -> str:
    """Think through a problem privately without showing output to the user.

    Use this to reason about complex decisions, plan tool use sequences,
    evaluate alternatives, or work through logic before acting. Your
    reasoning is recorded to the workspace trace for debugging but is
    not included in the user-facing response.

    Args:
        reasoning: Your private reasoning, analysis, or planning notes.
    """
    uid = _get_user_id()
    if uid and uid != "unknown":
        try:
            workspace_service.append_trace(uid, [{
                "type": TraceEvent.THINK,
                "content": f"[THINK] {reasoning}",
            }])
        except Exception:
            pass
    return "OK"


@risk_tool(risk=RiskLevel.HIGH)
def request_extended_budget(reason: str, additional_calls: int = 20) -> str:
    """Request additional tool calls beyond the current budget.

    Use this when you are mid-task and running low on tool calls.
    The request requires user confirmation (HIGH risk gate).

    Args:
        reason: Why you need more tool calls (shown to the user).
        additional_calls: How many additional calls to request (default 20, max 50).
    """
    from prax.agent.governed_tool import extend_budget, get_budget_status

    capped = min(max(additional_calls, 5), 50)
    extend_budget(capped)

    used, new_budget = get_budget_status()
    return (
        f"Budget extended by {capped} calls. "
        f"Current usage: {used}/{new_budget} calls."
    )


def build_workspace_tools():

    tools = [
        user_notes_update, user_notes_read, reread_instructions,
        workspace_save, workspace_patch, workspace_read, workspace_list,
        workspace_send_file, latex_compile,
        workspace_archive, workspace_search, workspace_restore,
        log_link, links_history,
        todo_add, todo_list, todo_complete, todo_remove,
        agent_plan, agent_step_done, agent_plan_status, agent_plan_clear,
        conversation_history, conversation_search,
        read_logs, system_status,
        think, request_extended_budget,
    ]

    return tools
