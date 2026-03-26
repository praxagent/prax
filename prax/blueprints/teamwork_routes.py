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


# TeamWork user ID — formatted like a phone number so it works with
# the phone-int-based conversation storage in conversation_service.
_TEAMWORK_USER_ID = "+10000000001"


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

            # Show typing indicator
            tw.send_typing(channel="general", agent_name="Prax")

            # Process through Prax's normal conversation pipeline
            response = conversation_service.reply(_TEAMWORK_USER_ID, content)

            # Send response back to TeamWork
            tw.send_message(content=response, channel="general", agent_name="Prax")

        except Exception:
            logger.exception("Failed to process TeamWork message")
            try:
                from prax.services.teamwork_service import get_teamwork_client
                tw = get_teamwork_client()
                tw.send_message(
                    content="Sorry, I encountered an error processing your message.",
                    channel="general",
                    agent_name="Prax",
                )
            except Exception:
                pass
