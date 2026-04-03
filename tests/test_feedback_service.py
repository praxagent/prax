"""Tests for the feedback service and failure journal."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from prax.services.feedback_service import (
    FeedbackEntry,
    get_feedback,
    get_feedback_stats,
    submit_feedback,
)

# ---------------------------------------------------------------------------
# FeedbackEntry
# ---------------------------------------------------------------------------


class TestFeedbackEntry:
    def test_auto_generates_id_and_timestamp(self):
        entry = FeedbackEntry(user_id="u1", rating="positive")
        assert len(entry.id) == 12
        assert entry.created_at

    def test_preserves_explicit_id(self):
        entry = FeedbackEntry(id="custom123", user_id="u1", rating="negative")
        assert entry.id == "custom123"


# ---------------------------------------------------------------------------
# submit_feedback
# ---------------------------------------------------------------------------


class TestSubmitFeedback:
    def test_rejects_invalid_rating(self, tmp_path):
        with pytest.raises(ValueError, match="positive.*negative"):
            submit_feedback(user_id="u1", rating="meh")

    def test_positive_feedback_persists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.feedback_service._feedback_dir",
            lambda: tmp_path,
        )
        entry = submit_feedback(user_id="u1", rating="positive", comment="great")
        assert entry.rating == "positive"
        assert entry.comment == "great"

        # Check JSONL file
        filepath = tmp_path / "feedback.jsonl"
        assert filepath.exists()
        data = json.loads(filepath.read_text().strip())
        assert data["rating"] == "positive"

    def test_negative_feedback_creates_failure_case(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.feedback_service._feedback_dir",
            lambda: tmp_path,
        )
        mock_record = MagicMock()
        monkeypatch.setattr(
            "prax.services.memory.failure_journal.record_failure",
            mock_record,
        )

        entry = submit_feedback(
            user_id="u1",
            rating="negative",
            trace_id="trace123",
            message_content="bad response",
            comment="used wrong tool",
        )
        assert entry.rating == "negative"
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args
        assert call_kwargs[1]["user_id"] == "u1" or call_kwargs[0][0] == "u1"


# ---------------------------------------------------------------------------
# get_feedback
# ---------------------------------------------------------------------------


class TestGetFeedback:
    def test_filters_by_rating(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.feedback_service._feedback_dir",
            lambda: tmp_path,
        )
        # Suppress failure journal creation for negative feedback
        monkeypatch.setattr(
            "prax.services.feedback_service._create_failure_case",
            lambda f: None,
        )

        submit_feedback(user_id="u1", rating="positive")
        submit_feedback(user_id="u1", rating="negative", comment="bad")
        submit_feedback(user_id="u1", rating="positive")

        all_entries = get_feedback(user_id="u1")
        assert len(all_entries) == 3

        neg_only = get_feedback(user_id="u1", rating_filter="negative")
        assert len(neg_only) == 1
        assert neg_only[0].rating == "negative"

    def test_returns_most_recent_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.feedback_service._feedback_dir",
            lambda: tmp_path,
        )
        submit_feedback(user_id="u1", rating="positive", comment="first")
        submit_feedback(user_id="u1", rating="positive", comment="second")

        entries = get_feedback(user_id="u1")
        assert entries[0].comment == "second"


# ---------------------------------------------------------------------------
# get_feedback_stats
# ---------------------------------------------------------------------------


class TestFeedbackStats:
    def test_stats_computation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.feedback_service._feedback_dir",
            lambda: tmp_path,
        )
        monkeypatch.setattr(
            "prax.services.feedback_service._create_failure_case",
            lambda f: None,
        )

        submit_feedback(user_id="u1", rating="positive")
        submit_feedback(user_id="u1", rating="positive")
        submit_feedback(user_id="u1", rating="negative")

        stats = get_feedback_stats(user_id="u1")
        assert stats["total"] == 3
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert stats["positive_rate"] == pytest.approx(0.667, abs=0.01)

    def test_empty_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prax.services.feedback_service._feedback_dir",
            lambda: tmp_path,
        )
        stats = get_feedback_stats(user_id="u1")
        assert stats["total"] == 0
        assert stats["positive_rate"] == 0.0
