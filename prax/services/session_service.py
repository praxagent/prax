"""Session grouping service — clusters related traces into sessions.

Uses a cheap LLM to classify whether each new user message is a
continuation of the previous session or the start of a new one.
Sessions are identified by a session_id stored on each ExecutionGraph.

The LLM sees only the previous session's topic summary and the new
user message — minimal tokens, fast, cheap.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Per-user session state (in-memory, keyed by user_id)
_user_sessions: dict[str, dict] = {}

# If the gap between messages exceeds this, always start a new session
_MAX_GAP_SECONDS = 1800  # 30 minutes


def _get_user_state(user_id: str) -> dict:
    """Get or create session state for a user."""
    if user_id not in _user_sessions:
        _user_sessions[user_id] = {
            "session_id": None,
            "topic_summary": "",
            "last_message_at": None,
            "turn_count": 0,
            "recent_turns": [],  # last few (user_msg, response) pairs
        }
    return _user_sessions[user_id]


def _is_continuation_llm(topic_summary: str, new_message: str, recent_turns: list) -> bool:
    """Ask a cheap LLM whether the new message continues the current session."""
    try:
        from prax.agent.llm_factory import build_llm

        llm = build_llm(config_key="session_classifier", default_tier="low")

        # Build conversation history for context
        history = ""
        if recent_turns:
            history = "Recent conversation:\n"
            for user_msg, response in recent_turns[-3:]:  # last 3 turns
                history += f"  User: {user_msg[:100]}\n"
                history += f"  Assistant: {response[:100]}\n"
            history += "\n"

        prompt = (
            "You are a conversation classifier. Given the session context "
            "and a new user message, answer ONLY 'yes' or 'no'.\n\n"
            "Is this new message a continuation of the same conversation/task? "
            "Short replies like 'do it', 'yes', 'ok', 'fix that' are almost always "
            "continuations. A new topic is something completely unrelated.\n\n"
            f"Session topic: {topic_summary}\n\n"
            f"{history}"
            f"New message: {new_message}\n\n"
            "Answer (yes or no):"
        )
        response = llm.invoke(prompt)
        answer = response.content.strip().lower()
        return answer.startswith("yes")
    except Exception:
        logger.debug("Session classifier LLM failed, defaulting to continuation", exc_info=True)
        # Default to continuation on failure — splitting is worse than over-grouping
        return True


def _update_topic_summary(old_summary: str, new_message: str, response: str) -> str:
    """Update the session topic summary with the latest turn."""
    try:
        from prax.agent.llm_factory import build_llm

        llm = build_llm(config_key="session_classifier", default_tier="low")
        prompt = (
            "Summarize this conversation session in one brief sentence (max 15 words). "
            "Focus on the main task or topic.\n\n"
            f"Previous summary: {old_summary or '(new session)'}\n"
            f"Latest user message: {new_message[:200]}\n"
            f"Latest response: {response[:200]}\n\n"
            "Summary:"
        )
        result = llm.invoke(prompt)
        return result.content.strip()[:150]
    except Exception:
        # Fallback: use first 100 chars of user message
        return new_message[:100]


def classify_session(user_id: str, user_message: str) -> str:
    """Determine the session_id for a new trace.

    Returns an existing session_id if the message continues the current
    session, or generates a new one if it's a new topic.

    This is called at the START of each orchestrator turn, before the
    agent processes the message.
    """
    state = _get_user_state(user_id)
    now = datetime.now(UTC)

    # Skip classification for system messages
    if user_message.startswith("[SCHEDULED_TASK") or user_message.startswith("[Reminder]"):
        return f"scheduled-{uuid.uuid4().hex[:8]}"

    # Rule 1: No existing session → always new
    if not state["session_id"]:
        state["session_id"] = uuid.uuid4().hex[:12]
        state["topic_summary"] = user_message[:100]
        state["last_message_at"] = now
        state["turn_count"] = 1
        logger.debug("New session %s (first message)", state["session_id"])
        return state["session_id"]

    # Rule 2: Time gap > 30 min → always new
    if state["last_message_at"]:
        gap = (now - state["last_message_at"]).total_seconds()
        if gap > _MAX_GAP_SECONDS:
            state["session_id"] = uuid.uuid4().hex[:12]
            state["topic_summary"] = user_message[:100]
            state["last_message_at"] = now
            state["turn_count"] = 1
            logger.debug("New session %s (gap=%.0fs)", state["session_id"], gap)
            return state["session_id"]

    # Rule 3: Ask the LLM
    is_continuation = _is_continuation_llm(
        state["topic_summary"], user_message, state.get("recent_turns", []),
    )

    if is_continuation:
        state["last_message_at"] = now
        state["turn_count"] += 1
        logger.debug("Continuing session %s (turn %d)", state["session_id"], state["turn_count"])
        return state["session_id"]

    # New session
    state["session_id"] = uuid.uuid4().hex[:12]
    state["topic_summary"] = user_message[:100]
    state["last_message_at"] = now
    state["turn_count"] = 1
    logger.debug("New session %s (topic change)", state["session_id"])
    return state["session_id"]


def update_session_summary(user_id: str, user_message: str, response: str) -> None:
    """Update the session topic summary after a turn completes.

    Called at the END of each orchestrator turn so the next classification
    has an accurate summary to compare against.
    """
    state = _get_user_state(user_id)
    if not state["session_id"]:
        return

    # Track recent turns for classifier context
    turns = state.get("recent_turns", [])
    turns.append((user_message[:200], response[:200]))
    state["recent_turns"] = turns[-5:]  # keep last 5 turns

    state["topic_summary"] = _update_topic_summary(
        state["topic_summary"], user_message, response,
    )
