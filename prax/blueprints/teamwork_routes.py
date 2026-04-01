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
            from prax.agent.user_context import current_active_view, current_channel_id
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
            else:
                tool_guidance = (
                    "The user is NOT watching the browser right now, but delegate_browser "
                    "is still available for any task that needs real browser rendering — "
                    "JS-heavy pages, login flows, form filling, sites where fetch_url_content "
                    "returns empty/broken content. Use it freely when HTTP tools fail."
                )

            if is_dm:
                channel_hint = (
                    f"[via TeamWork web UI — private DM. {view_hint}{tool_guidance}]\n"
                )
            else:
                channel_hint = (
                    f"[via TeamWork web UI — public channel. {view_hint}{tool_guidance}]\n"
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
