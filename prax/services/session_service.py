"""Session grouping service — clusters related traces into sessions.

Uses a cheap LLM to classify whether each new user message is a
continuation of the previous session or the start of a new one.
Sessions are identified by a session_id stored on each ExecutionGraph.

The LLM sees only the previous session's topic summary and the new
user message — minimal tokens, fast, cheap.
"""
from __future__ import annotations

import hashlib
import logging
import re
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
            "topic_tokens": set(),
        }
    return _user_sessions[user_id]


_SYSTEM_PREFIX_RE = re.compile(r"^\[(?:SCHEDULED_TASK|Reminder)[^\]]*\]\s*", re.IGNORECASE)
_INLINE_SYSTEM_RE = re.compile(r"\[SYSTEM:[^\]]+\]", re.IGNORECASE)
_URLISH_RE = re.compile(r"https?://\S+|[a-z0-9.-]+\.[a-z]{2,}(?:/\S*)?", re.IGNORECASE)
_STOP_TOKENS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "what",
    "went", "wrong", "http", "https", "www", "com", "org", "net", "xyz",
    "blog", "page", "link", "url", "system", "captured", "library", "raw",
}


def _is_system_generated(user_message: str) -> bool:
    return user_message.startswith("[SCHEDULED_TASK") or user_message.startswith("[Reminder]")


def _normalize_message_for_session(user_message: str) -> str:
    """Remove transport/system noise before session classification."""
    text = _SYSTEM_PREFIX_RE.sub("", user_message or "")
    text = _INLINE_SYSTEM_RE.sub(" ", text)
    text = text.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _scheduled_session_id(user_message: str) -> str:
    normalized = _normalize_message_for_session(user_message).lower()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"scheduled-{digest}"


def _message_topic_tokens(message: str) -> set[str]:
    """Extract stable topic tokens, including URL slugs/domains."""
    normalized = _normalize_message_for_session(message).lower()
    urlish = " ".join(_URLISH_RE.findall(normalized))
    haystack = f"{normalized} {urlish}".replace("/", " ").replace(".", " ")
    tokens = set(re.findall(r"[a-z0-9]{3,}", haystack))
    return {t for t in tokens if t not in _STOP_TOKENS}


def _topic_overlap_continuation(state: dict, new_message: str) -> bool:
    new_tokens = _message_topic_tokens(new_message)
    if len(new_tokens) < 2:
        return False
    old_tokens = set(state.get("topic_tokens") or set())
    for user_msg, _response in state.get("recent_turns", [])[-5:]:
        old_tokens.update(_message_topic_tokens(user_msg))
    if len(old_tokens) < 2:
        return False
    overlap = new_tokens & old_tokens
    return len(overlap) >= 2 and len(overlap) / max(1, min(len(new_tokens), len(old_tokens))) >= 0.25


def _start_new_session(state: dict, normalized_message: str, now: datetime) -> str:
    state["session_id"] = uuid.uuid4().hex[:12]
    state["topic_summary"] = normalized_message[:100]
    state["last_message_at"] = now
    state["turn_count"] = 1
    state["topic_tokens"] = _message_topic_tokens(normalized_message)
    return state["session_id"]


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
    normalized_message = _normalize_message_for_session(user_message)

    # System-generated notifications should be grouped by the scheduled job
    # prompt, not random UUIDs, and must not mutate the user's active chat
    # session state.
    if _is_system_generated(user_message):
        return _scheduled_session_id(user_message)

    # Rule 1: No existing session → always new
    if not state["session_id"]:
        _start_new_session(state, normalized_message, now)
        logger.debug("New session %s (first message)", state["session_id"])
        return state["session_id"]

    # Rule 2: Time gap > 30 min → always new
    if state["last_message_at"]:
        gap = (now - state["last_message_at"]).total_seconds()
        if gap > _MAX_GAP_SECONDS:
            _start_new_session(state, normalized_message, now)
            logger.debug("New session %s (gap=%.0fs)", state["session_id"], gap)
            return state["session_id"]

    # Rule 3: Deterministic topic continuity for URL/capture variants.  This
    # avoids splitting "same article, corrected URL" traces before the cheap
    # LLM classifier gets a chance to overthink the host change.
    if _topic_overlap_continuation(state, normalized_message):
        state["last_message_at"] = now
        state["turn_count"] += 1
        state["topic_tokens"] = set(state.get("topic_tokens") or set()) | _message_topic_tokens(normalized_message)
        logger.debug("Continuing session %s (topic-token overlap)", state["session_id"])
        return state["session_id"]

    # Rule 4: Ask the LLM
    is_continuation = _is_continuation_llm(
        state["topic_summary"], normalized_message, state.get("recent_turns", []),
    )

    if is_continuation:
        state["last_message_at"] = now
        state["turn_count"] += 1
        state["topic_tokens"] = set(state.get("topic_tokens") or set()) | _message_topic_tokens(normalized_message)
        logger.debug("Continuing session %s (turn %d)", state["session_id"], state["turn_count"])
        return state["session_id"]

    # New session
    _start_new_session(state, normalized_message, now)
    logger.debug("New session %s (topic change)", state["session_id"])
    return state["session_id"]


def update_session_summary(user_id: str, user_message: str, response: str) -> None:
    """Update the session topic summary after a turn completes.

    Called at the END of each orchestrator turn so the next classification
    has an accurate summary to compare against.
    """
    if _is_system_generated(user_message):
        return

    state = _get_user_state(user_id)
    if not state["session_id"]:
        return

    normalized_message = _normalize_message_for_session(user_message)

    # Track recent turns for classifier context
    turns = state.get("recent_turns", [])
    turns.append((normalized_message[:200], response[:200]))
    state["recent_turns"] = turns[-5:]  # keep last 5 turns
    state["topic_tokens"] = set(state.get("topic_tokens") or set()) | _message_topic_tokens(normalized_message)

    state["topic_summary"] = _update_topic_summary(
        state["topic_summary"], normalized_message, response,
    )
