"""Tests for session grouping service."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from prax.services.session_service import (
    _MAX_GAP_SECONDS,
    _get_user_state,
    _user_sessions,
    classify_session,
    update_session_summary,
)


def _reset():
    _user_sessions.clear()


class TestClassifySession:
    def test_first_message_creates_new_session(self):
        _reset()
        sid = classify_session("user1", "Hello world")
        assert sid
        assert len(sid) == 12
        state = _get_user_state("user1")
        assert state["session_id"] == sid
        assert state["turn_count"] == 1

    def test_scheduled_task_gets_standalone_session(self):
        _reset()
        sid = classify_session("user1", "[SCHEDULED_TASK — ...] check email")
        assert sid.startswith("scheduled-")

    def test_scheduled_task_session_is_stable_for_same_prompt(self):
        _reset()
        prompt = "[SCHEDULED_TASK — ...] Send the morning briefing"
        sid1 = classify_session("user1", prompt)
        sid2 = classify_session("user1", prompt)
        assert sid1 == sid2

    def test_scheduled_task_does_not_mutate_active_chat_session(self):
        _reset()
        sid1 = classify_session("user1", "Discuss the geometry of surprise article")
        update_session_summary("user1", "[SCHEDULED_TASK — ...] Send the morning briefing", "Done")
        state = _get_user_state("user1")
        assert state["session_id"] == sid1
        assert "morning" not in state["topic_summary"].lower()

    def test_reminder_gets_standalone_session(self):
        _reset()
        sid = classify_session("user1", "[Reminder] go to dentist")
        assert sid.startswith("scheduled-")

    def test_time_gap_creates_new_session(self):
        _reset()
        from datetime import UTC, datetime, timedelta

        sid1 = classify_session("user1", "First message")
        state = _get_user_state("user1")
        # Simulate a long gap
        state["last_message_at"] = datetime.now(UTC) - timedelta(seconds=_MAX_GAP_SECONDS + 60)

        sid2 = classify_session("user1", "Second message after long gap")
        assert sid1 != sid2
        assert state["turn_count"] == 1  # reset for new session

    def test_continuation_uses_same_session(self):
        _reset()
        sid1 = classify_session("user1", "Tell me about quantum computing")

        # Mock the LLM to say "yes, continuation"
        with patch("prax.services.session_service._is_continuation_llm", return_value=True):
            sid2 = classify_session("user1", "What about the latest breakthroughs?")

        assert sid1 == sid2
        state = _get_user_state("user1")
        assert state["turn_count"] == 2

    def test_topic_change_creates_new_session(self):
        _reset()
        sid1 = classify_session("user1", "Tell me about quantum computing")

        with patch("prax.services.session_service._is_continuation_llm", return_value=False):
            sid2 = classify_session("user1", "What's the weather?")

        assert sid1 != sid2

    def test_url_slug_overlap_continues_session_even_if_host_changes(self):
        _reset()
        sid1 = classify_session("user1", "https://sethmorton.xyz/blog/the_geometry_of_surprise")

        corrected_url = (
            "https://www.sethmorton.com/blog/the_geometry_of_surprise\n\n"
            "[SYSTEM: captured to library/raw/ as `20260426-143300-www-sethmorton-com-blog-the-geometry-of-surprise`]"
        )
        with patch("prax.services.session_service._is_continuation_llm", return_value=False):
            sid2 = classify_session("user1", corrected_url)

        assert sid1 == sid2

    def test_different_users_get_different_sessions(self):
        _reset()
        sid1 = classify_session("user1", "Hello")
        sid2 = classify_session("user2", "Hello")
        assert sid1 != sid2


class TestUpdateSessionSummary:
    def test_updates_topic_summary(self):
        _reset()
        classify_session("user1", "Research quantum computing")

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="Researching quantum computing advances")

        with patch("prax.agent.llm_factory.build_llm", return_value=mock_llm):
            update_session_summary("user1", "Research quantum computing", "Here are the latest advances...")

        state = _get_user_state("user1")
        assert "quantum" in state["topic_summary"].lower()

    def test_no_crash_on_missing_session(self):
        _reset()
        # Should not raise
        update_session_summary("nonexistent", "hello", "world")
