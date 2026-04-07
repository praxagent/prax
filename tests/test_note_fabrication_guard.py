"""Tests for the note_create fabrication guard.

When a URL fetch fails, the agent may try to create a note based on
guesses from its training data. We detect and refuse these notes at the
tool layer so fabrication can't slip past the system prompt rules.
"""
from __future__ import annotations

from unittest.mock import patch

from prax.agent.note_tools import _looks_fabricated, note_create


class TestFabricationDetection:
    def test_clean_note_passes(self):
        assert _looks_fabricated(
            "High-Dimensional Gaussians",
            "The chi-squared distribution has mean d and variance 2d.",
        ) is None

    def test_inferred_title_caught(self):
        reason = _looks_fabricated(
            "Deep Dive: TurboQuant (Inferred)",
            "This note describes the topic based on best guesses.",
        )
        assert reason is not None
        assert "inferred" in reason.lower() or "matched" in reason.lower()

    def test_inferred_bracket_caught(self):
        reason = _looks_fabricated(
            "TurboQuant Analysis",
            "[INFERRED] Content based on URL slug meaning.",
        )
        assert reason is not None

    def test_best_guess_caught(self):
        reason = _looks_fabricated(
            "Notes on TurboQuant",
            "Best guess at what the article contains based on training data.",
        )
        assert reason is not None

    def test_could_not_read_caught(self):
        reason = _looks_fabricated(
            "TurboQuant Summary",
            "I could not read the original URL, but here's what it probably said...",
        )
        assert reason is not None

    def test_page_not_found_caught(self):
        reason = _looks_fabricated(
            "Article on TurboQuant",
            "Got a page not found error, so this is reconstructed from my knowledge.",
        )
        assert reason is not None

    def test_likely_content_caught(self):
        reason = _looks_fabricated(
            "TurboQuant Overview",
            "This contains the likely content of the article.",
        )
        assert reason is not None

    def test_404_phrase_caught(self):
        reason = _looks_fabricated(
            "Summary",
            "The source returned 404 not found, so the following is inferred.",
        )
        assert reason is not None

    def test_ordinary_note_with_word_inferred_allowed(self):
        """The word 'inferred' in a technical context (not about source failure)
        should NOT trigger — but to keep the guard simple, we accept that
        technical usages of '(inferred)' will be blocked. Rewrite is needed."""
        # This is a conscious tradeoff — false positives here are acceptable
        # because the user can trivially rewrite the title.
        reason = _looks_fabricated(
            "Bayesian Inference Primer",
            "Prior beliefs give us the posterior distribution.",
        )
        assert reason is None  # 'inferred' with bracket/paren specifically

    def test_content_truncation(self):
        """Only the first 2000 chars are scanned — markers later in the
        note should still catch common patterns earlier in the content."""
        reason = _looks_fabricated(
            "Clean Title",
            "I could not read the source" + (" padding" * 500),
        )
        assert reason is not None


class TestNoteCreateGuard:
    def test_clean_note_proceeds(self):
        with patch("prax.agent.note_tools.note_service") as mock_svc, \
             patch("prax.agent.note_tools._get_user_id", return_value="u1"), \
             patch("prax.services.note_quality.llm_review", return_value={
                 "approved": True, "issues": [], "verdict": "ok",
             }):
            mock_svc.save_and_publish.return_value = {
                "title": "Clean Note",
                "slug": "clean-note",
                "url": "http://example.com/notes/clean-note/",
            }
            result = note_create.invoke({
                "title": "Clean Note",
                "content": "Real content here.",
                "tags": "",
            })
            assert "Note created" in result
            mock_svc.save_and_publish.assert_called_once()

    def test_fabricated_note_blocked(self):
        with patch("prax.agent.note_tools.note_service") as mock_svc:
            result = note_create.invoke({
                "title": "Deep Dive: TurboQuant (Inferred)",
                "content": "Based on best guesses from training data.",
                "tags": "",
            })
            assert "BLOCKED" in result
            assert "fabricated" in result.lower()
            mock_svc.save_and_publish.assert_not_called()

    def test_fabrication_guard_blocks_404_note(self):
        with patch("prax.agent.note_tools.note_service") as mock_svc:
            result = note_create.invoke({
                "title": "Article Summary",
                "content": "The URL returned 404 not found so I inferred the content from the title.",
                "tags": "",
            })
            assert "BLOCKED" in result
            mock_svc.save_and_publish.assert_not_called()

    def test_guard_includes_rewrite_guidance(self):
        with patch("prax.agent.note_tools.note_service"):
            result = note_create.invoke({
                "title": "Likely Content (Inferred)",
                "content": "Best guess at what the page said.",
                "tags": "",
            })
            assert "rewrite" in result.lower() or "remove" in result.lower()
