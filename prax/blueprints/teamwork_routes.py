"""TeamWork webhook — receives user messages from TeamWork's UI."""
from __future__ import annotations

import logging
import threading

from flask import Blueprint, Flask, jsonify, request

logger = logging.getLogger(__name__)

teamwork_routes = Blueprint("teamwork", __name__)


@teamwork_routes.route("/teamwork/webhook", methods=["POST"])
def teamwork_webhook():
    """Receive a user message from TeamWork and process it asynchronously."""
    data = request.get_json(silent=True) or {}
    msg_type = data.get("type", "")
    content = data.get("content", "")
    channel_id = data.get("channel_id", "")
    project_id = data.get("project_id", "")
    message_id = data.get("message_id", "")

    if msg_type != "user_message" or not content:
        return jsonify({"status": "ignored"}), 200

    logger.info(
        "TeamWork webhook: project=%s channel=%s content=%s",
        project_id, channel_id, content[:80],
    )

    # Process asynchronously so we return 200 quickly.
    # Pass the Flask app so the thread can push an app context.
    from flask import current_app
    app = current_app._get_current_object()

    thread = threading.Thread(
        target=_handle_message,
        args=(app, project_id, channel_id, content, message_id),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "accepted"}), 200


def _get_teamwork_user_id() -> str:
    """Return the user identity for TeamWork messages.

    If TEAMWORK_USER_PHONE is configured, messages share history/workspace
    with SMS and Discord (same phone number = same conversation).
    Falls back to a synthetic phone number if not configured.
    """
    from prax.settings import settings
    return settings.teamwork_user_phone or "+10000000001"


def _handle_message(
    app: Flask,
    project_id: str,
    channel_id: str,
    content: str,
    message_id: str,
) -> None:
    """Process a TeamWork user message through Prax's conversation service."""
    with app.app_context():
        try:
            from prax.services.conversation_service import conversation_service
            from prax.services.teamwork_service import get_teamwork_client

            tw = get_teamwork_client()

            user_id = _get_teamwork_user_id()

            # Inject channel context so Prax knows the communication channel.
            prefixed_content = f"[via TeamWork web UI]\n{content}"

            # Keep typing indicator alive for the entire duration of processing.
            with tw.typing(channel_id=channel_id, agent_name="Prax"):
                response = conversation_service.reply(user_id, prefixed_content)

            # Send response back to the SAME channel (could be DM, #general, etc.)
            tw.send_message(content=response, channel_id=channel_id, agent_name="Prax")

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
