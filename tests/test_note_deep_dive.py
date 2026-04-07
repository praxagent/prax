"""Tests for the knowledge spoke's deep-dive pipeline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from prax.agent.spokes.knowledge.deep_dive import (
    _make_researcher,
    _note_publisher,
    note_deep_dive,
)


class TestMakeResearcher:
    def test_with_source_content_skips_research(self):
        researcher = _make_researcher("Pre-fetched article text")
        result = researcher("Topic", "")
        assert result == "Pre-fetched article text"

    def test_without_source_content_calls_subagent(self):
        researcher = _make_researcher(None)
        with patch("prax.agent.subagent._run_subagent", return_value="Research result") as mock_sub:
            result = researcher("Topic", "notes")
        assert result == "Research result"
        mock_sub.assert_called_once()
        # Category should be "research"
        args = mock_sub.call_args
        assert args[0][1] == "research"


class TestNotePublisher:
    def test_create_new_note(self):
        with patch("prax.services.note_service.save_and_publish") as mock_save, \
             patch("prax.agent.spokes.knowledge.deep_dive.current_user_id") as mock_ctx:
            mock_ctx.get.return_value = "user1"
            mock_save.return_value = {
                "slug": "test", "url": "http://x/test/", "title": "Test",
            }
            result = _note_publisher("Test", "content", tags=["tag1"])
            assert result["slug"] == "test"
            mock_save.assert_called_once_with(
                "user1", "Test", "content", tags=["tag1"],
            )

    def test_update_existing_slug(self):
        with patch("prax.services.note_service.update_note") as mock_update, \
             patch("prax.services.note_service.publish_notes") as mock_pub, \
             patch("prax.utils.ngrok.get_ngrok_url", return_value="http://ngrok.io"), \
             patch("prax.agent.spokes.knowledge.deep_dive.current_user_id") as mock_ctx:
            mock_ctx.get.return_value = "user1"
            mock_update.return_value = {"slug": "test", "title": "Test"}
            mock_pub.return_value = {"url": "http://ngrok.io/notes/test/"}
            result = _note_publisher("Test", "content", slug="test")
            assert result["url"] == "http://ngrok.io/notes/test/"
            mock_update.assert_called_once()

    def test_publisher_error(self):
        with patch("prax.services.note_service.save_and_publish",
                   side_effect=RuntimeError("disk full")), \
             patch("prax.agent.spokes.knowledge.deep_dive.current_user_id") as mock_ctx:
            mock_ctx.get.return_value = "user1"
            result = _note_publisher("Test", "content")
            assert "error" in result
            assert "disk full" in result["error"]


class TestNoteDeepDiveTool:
    def test_happy_path_with_source(self):
        """End-to-end happy path with pre-fetched source content."""
        mock_writer_response = """# Deep Dive: TurboQuant

## Introduction
The key insight is that orthogonal rotations smear outliers.

## Toy Example
Consider $K = [0.1, -0.2, 8.0, 0.4]$. The outlier 8.0 dominates."""
        mock_review = "APPROVED\n\nGreat synthesis, clear toy example."

        with patch("prax.agent.spokes.knowledge.deep_dive._note_writer",
                   return_value=mock_writer_response) as mock_writer, \
             patch("prax.agent.spokes.knowledge.deep_dive._note_reviewer",
                   return_value=mock_review) as mock_reviewer, \
             patch("prax.agent.spokes.knowledge.deep_dive._note_publisher",
                   return_value={"slug": "test", "url": "http://x/test/"}) as mock_pub, \
             patch("prax.agent.spokes.knowledge.deep_dive._post_status"), \
             patch("prax.agent.spokes.knowledge.deep_dive._finish"):
            result = note_deep_dive.invoke({
                "topic": "Breaking Down TurboQuant",
                "source_content": "Full article text about KV cache compression...",
                "tags": "ml, quantization",
            })

            assert "approved" in result.lower()
            assert "http://x/test/" in result
            mock_writer.assert_called_once()
            mock_reviewer.assert_called_once()
            mock_pub.assert_called_once()

    def test_revision_cycle(self):
        """Reviewer rejects first pass, approves second."""
        writer = MagicMock(side_effect=["# Draft 1", "# Draft 2 (improved)"])
        reviewer = MagicMock(side_effect=[
            "REVISE\n\nNo toy example in Section 2.",
            "APPROVED\n\nMuch better.",
        ])

        with patch("prax.agent.spokes.knowledge.deep_dive._note_writer", writer), \
             patch("prax.agent.spokes.knowledge.deep_dive._note_reviewer", reviewer), \
             patch("prax.agent.spokes.knowledge.deep_dive._note_publisher",
                   return_value={"slug": "test", "url": "http://x/test/"}), \
             patch("prax.agent.spokes.knowledge.deep_dive._post_status"), \
             patch("prax.agent.spokes.knowledge.deep_dive._finish"):
            result = note_deep_dive.invoke({
                "topic": "Test Topic",
                "source_content": "source",
            })

            assert "approved" in result.lower()
            assert writer.call_count == 2
            assert reviewer.call_count == 2

    def test_exhausted_revisions(self):
        """Reviewer never approves — pipeline publishes after max revisions."""
        reviewer = MagicMock(return_value="REVISE\n\nStill not good enough.")

        with patch("prax.agent.spokes.knowledge.deep_dive._note_writer",
                   return_value="# Draft") as _, \
             patch("prax.agent.spokes.knowledge.deep_dive._note_reviewer", reviewer), \
             patch("prax.agent.spokes.knowledge.deep_dive._note_publisher",
                   return_value={"slug": "test", "url": "http://x/test/"}), \
             patch("prax.agent.spokes.knowledge.deep_dive._post_status"), \
             patch("prax.agent.spokes.knowledge.deep_dive._finish"):
            result = note_deep_dive.invoke({
                "topic": "Test Topic",
                "source_content": "source",
            })

            assert "did not fully approve" in result
            assert reviewer.call_count == 3

    def test_write_failure_reports_error(self):
        with patch("prax.agent.spokes.knowledge.deep_dive._note_writer",
                   return_value="Writer failed: LLM timeout"), \
             patch("prax.agent.spokes.knowledge.deep_dive._note_publisher") as mock_pub, \
             patch("prax.agent.spokes.knowledge.deep_dive._post_status"), \
             patch("prax.agent.spokes.knowledge.deep_dive._finish"):
            result = note_deep_dive.invoke({
                "topic": "Test Topic",
                "source_content": "source",
            })

            assert "Writing phase failed" in result
            mock_pub.assert_not_called()
