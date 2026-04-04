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
    """
    from prax.agent.trace import last_root_trace_id

    trace_id = last_root_trace_id.get()
    if not trace_id:
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
    "content": "Prax's Space — browsing notes, courses, and news",
}


# ---------------------------------------------------------------------------
# Content API — notes, courses, news for Prax's Space panel
# ---------------------------------------------------------------------------

@teamwork_routes.route("/teamwork/content", methods=["GET"])
def list_content():
    """Return all notes, courses, and news for the current user."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.course_service import list_courses
        from prax.services.note_service import list_news, list_notes

        return jsonify({
            "notes": list_notes(user_id),
            "courses": list_courses(user_id),
            "news": list_news(user_id),
        })
    except Exception:
        logger.exception("Failed to list content")
        return jsonify({"error": "Failed to list content"}), 500


@teamwork_routes.route("/teamwork/content/<category>/<slug>", methods=["GET"])
def get_content_item(category: str, slug: str):
    """Return a single content item (note, course, or news) with full content."""
    try:
        user_id = _get_teamwork_user_id()

        if category == "notes":
            from prax.services.note_service import get_note
            return jsonify(get_note(user_id, slug))
        elif category == "courses":
            from prax.services.course_service import get_course
            return jsonify(get_course(user_id, slug))
        elif category == "news":
            import os

            from prax.services.note_service import _news_dir, _parse_note
            from prax.services.workspace_service import ensure_workspace, get_lock
            with get_lock(user_id):
                root = ensure_workspace(user_id)
                news_root = _news_dir(root)
                path = os.path.join(news_root, f"{slug}.md")
                if not os.path.isfile(path):
                    return jsonify({"error": f"News item not found: {slug}"}), 404
                return jsonify(_parse_note(path))
        else:
            return jsonify({"error": f"Unknown category: {category}"}), 400
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        logger.exception("Failed to get content item")
        return jsonify({"error": "Failed to get content item"}), 500


@teamwork_routes.route("/teamwork/content/search", methods=["GET"])
def search_content():
    """Search across notes, courses, and news."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"notes": [], "courses": [], "news": []})

    try:
        user_id = _get_teamwork_user_id()
        from prax.services.course_service import list_courses
        from prax.services.note_service import search_news, search_notes

        # Courses don't have a search function — filter client-side.
        query_lower = query.lower()
        all_courses = list_courses(user_id)
        matched_courses = [
            c for c in all_courses
            if query_lower in c.get("title", "").lower()
            or query_lower in c.get("subject", "").lower()
        ]

        return jsonify({
            "notes": search_notes(user_id, query),
            "courses": matched_courses,
            "news": search_news(user_id, query),
        })
    except Exception:
        logger.exception("Failed to search content")
        return jsonify({"error": "Failed to search content"}), 500


@teamwork_routes.route("/teamwork/content/notes/<slug>", methods=["DELETE"])
def delete_content_note(slug: str):
    """Delete a note."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.note_service import delete_note
        return jsonify(delete_note(user_id, slug))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        logger.exception("Failed to delete note")
        return jsonify({"error": "Failed to delete note"}), 500


@teamwork_routes.route("/teamwork/content/notes/<slug>", methods=["PUT"])
def update_content_note(slug: str):
    """Update a note's content and/or title."""
    try:
        user_id = _get_teamwork_user_id()
        data = request.get_json(silent=True) or {}
        from prax.services.note_service import update_note
        meta = update_note(
            user_id, slug,
            content=data.get("content"),
            title=data.get("title"),
            tags=data.get("tags"),
        )
        return jsonify(meta)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        logger.exception("Failed to update note")
        return jsonify({"error": "Failed to update note"}), 500


@teamwork_routes.route("/teamwork/content/notes/<slug>/versions", methods=["GET"])
def list_note_versions(slug: str):
    """Return recent git versions of a note."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.note_service import note_versions
        limit = int(request.args.get("limit", "5"))
        return jsonify({"versions": note_versions(user_id, slug, limit=limit)})
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        logger.exception("Failed to list note versions")
        return jsonify({"error": "Failed to list note versions"}), 500


@teamwork_routes.route("/teamwork/content/notes/<slug>/versions/<commit>", methods=["GET"])
def get_note_at_version(slug: str, commit: str):
    """Return the note content at a specific git commit."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.note_service import get_note_version
        return jsonify(get_note_version(user_id, slug, commit))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        logger.exception("Failed to get note version")
        return jsonify({"error": "Failed to get note version"}), 500


@teamwork_routes.route("/teamwork/content/notes/<slug>/versions/<commit>/restore", methods=["POST"])
def restore_note_to_version(slug: str, commit: str):
    """Restore a note to a specific git version."""
    try:
        user_id = _get_teamwork_user_id()
        from prax.services.note_service import restore_note_version
        return jsonify(restore_note_version(user_id, slug, commit))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception:
        logger.exception("Failed to restore note version")
        return jsonify({"error": "Failed to restore note version"}), 500


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
            elif active_view == "content":
                # Include which content item the user is currently viewing
                content_ctx = (extra_data or {}).get("content_context")
                if content_ctx and isinstance(content_ctx, dict):
                    viewing_hint = (
                        f"The user is currently viewing: {content_ctx.get('category', 'notes')}/"
                        f"{content_ctx.get('slug', '?')} — \"{content_ctx.get('title', '?')}\". "
                        "The note content is included below in [CONTENT PANEL]. "
                        "When they say 'this page', 'this note', etc., they mean this item. "
                    )
                else:
                    viewing_hint = ""
                tool_guidance = (
                    f"The user is browsing Prax's Space (notes, courses, news). "
                    f"{viewing_hint}"
                    "They can read, edit, delete notes and view version history directly "
                    "in the UI. If the user says '[I just edited the note...]' or "
                    "'[I restored the note...]', that edit already happened — acknowledge "
                    "it and use the updated content going forward. "
                    "The user may ask you to update notes, create new ones, or discuss "
                    "content they're viewing. Use note_read, note_create, note_update "
                    "tools as needed."
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
            current_channel_id.set(channel_id)
            current_active_view.set(active_view)

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
