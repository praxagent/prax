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
        args=(app, project_id, channel_id, content, message_id, active_view),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "accepted"}), 200


def _build_trace_metadata() -> dict | None:
    """Build observability metadata to attach to the agent's response message.

    Returns a dict with trace_id and Grafana deep-link, or None if
    observability is disabled or no trace is available.
    """
    from prax.settings import settings
    if not settings.observability_enabled:
        return None

    from prax.agent.trace import last_root_trace_id
    trace_id = last_root_trace_id.get()
    if not trace_id:
        return None

    metadata: dict = {"trace_id": trace_id}
    if settings.grafana_url:
        # Deep-link to Tempo trace search in Grafana
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
    "settings": "the settings page",
    "progress": "the progress/coaching tab",
}


def _handle_message(
    app: Flask,
    project_id: str,
    channel_id: str,
    content: str,
    message_id: str,
    active_view: str = "",
) -> None:
    """Process a TeamWork user message through Prax's conversation service."""
    with app.app_context():
        try:
            from prax.agent.user_context import current_channel_id
            from prax.services.conversation_service import conversation_service
            from prax.services.teamwork_service import get_teamwork_client

            tw = get_teamwork_client()

            user_id = _get_teamwork_user_id()

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
                    "They can see the live browser — when they reference what's on screen "
                    "or ask you to interact with the page, use delegate_browser."
                )
            elif active_view == "terminal":
                tool_guidance = (
                    "They can see the live terminal — when they ask you to run code, "
                    "execute scripts, or do anything computational, prefer delegate_sandbox. "
                    "The user will watch the execution in real-time."
                )
            else:
                tool_guidance = (
                    "Only use delegate_browser when the user explicitly asks for browser "
                    "interaction (\"in the browser\", \"open\", \"navigate to\")."
                )

            if is_dm:
                channel_hint = (
                    f"[via TeamWork web UI — private DM. {view_hint}{tool_guidance}]\n"
                )
            else:
                channel_hint = (
                    f"[via TeamWork web UI — public channel. {view_hint}{tool_guidance}]\n"
                )

            prefixed_content = f"{channel_hint}{content}"

            # Always set channel context so agent hooks know which channel
            # originated the request (used for response routing).
            current_channel_id.set(channel_id)

            # Derive a per-channel conversation key so each TeamWork channel
            # gets its own isolated conversation history.
            channel_key = int(hashlib.sha256(channel_id.encode()).hexdigest()[:15], 16)

            # Keep typing indicator alive for the entire duration of processing.
            with tw.typing(channel_id=channel_id, agent_name="Prax"):
                response = conversation_service.reply(
                    user_id, prefixed_content, conversation_key=channel_key,
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
