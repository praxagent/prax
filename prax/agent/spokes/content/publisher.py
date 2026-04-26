"""Publishing helpers — thin wrapper around the note service for Hugo output."""
from __future__ import annotations

import logging

from prax.agent.user_context import current_user_id

logger = logging.getLogger(__name__)


def _get_user_id() -> str:
    uid = current_user_id.get()
    return uid or "unknown"


def publish_draft(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Create a new note and publish it via Hugo.

    Returns dict with ``slug``, ``url``, and ``title`` on success,
    or ``error`` on failure.
    """
    from prax.services.note_service import save_and_publish

    user_id = _get_user_id()
    try:
        result = save_and_publish(user_id, title, content, tags=tags)
        logger.info("Published draft '%s' → %s", title, result.get("url"))
        return result
    except Exception as exc:
        logger.exception("Failed to publish draft '%s'", title)
        return {"error": str(exc)}


def update_draft(slug: str, content: str, title: str | None = None) -> dict:
    """Update an existing published note and rebuild Hugo.

    Returns dict with ``slug``, ``url``, and ``title`` on success.
    """
    from prax.services.note_service import publish_notes, update_note
    from prax.settings import settings

    user_id = _get_user_id()
    teamwork_url = settings.teamwork_base_url.rstrip("/")
    try:
        result = update_note(user_id, slug, content=content, title=title)
        pub = publish_notes(user_id, teamwork_url, slug=slug)
        if "url" in pub:
            result["url"] = pub["url"]
        logger.info("Updated draft '%s' → %s", slug, result.get("url"))
        return result
    except Exception as exc:
        logger.exception("Failed to update draft '%s'", slug)
        return {"error": str(exc)}
