"""Tests for the note quality reviewer."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from prax.services import note_quality


@pytest.fixture(autouse=True)
def _clean():
    """Reset revision counters between tests."""
    note_quality._revision_counts.clear()
    yield
    note_quality._revision_counts.clear()


class TestHeuristicCheck:
    def test_clean_note_passes(self):
        title = "High-Dimensional Gaussians"
        content = """\
# High-Dimensional Gaussians

## Introduction
The chi-squared distribution gives us the typical radius of samples from
a high-dimensional Gaussian. The key insight is that volume grows faster
than density shrinks.

## Toy Example: d = 2
Consider a 2D Gaussian. The density at the origin is $\\frac{1}{2\\pi}$.
Note that even though the density peaks at the origin, the mass is in a ring.

## Conclusion
This means we can't treat high-dim Gaussians as hollow shells.
"""
        assert note_quality.heuristic_check(title, content) == []

    def test_orphan_comma_caught(self):
        content = """\
# Title

Here's some text
,
and here's more text that references $K = [0.1, 0.2]$.
"""
        issues = note_quality.heuristic_check("T", content)
        assert any("orphan comma" in i for i in issues)

    def test_image_placeholder_caught(self):
        content = """\
# Title

Some text here [Image] and more here.
"""
        issues = note_quality.heuristic_check("T", content)
        assert any("Image" in i for i in issues)

    def test_no_headings_long_note_caught(self):
        content = "\n".join(["This is line " + str(i) for i in range(50)])
        issues = note_quality.heuristic_check("T", content)
        assert any("headings" in i for i in issues)

    def test_no_explanatory_prose_caught(self):
        # 30+ lines of bullet points with no transitions
        content = "\n".join([
            "# Title",
            "## Section",
        ] + [f"- fact {i}" for i in range(30)])
        issues = note_quality.heuristic_check("T", content)
        assert any("explanatory" in i for i in issues)

    def test_duplicated_variable_artifact(self):
        # MathJax rendering twice
        content = """\
# Title
## Section
The key insight is that K = [0.1, 0.2] K = [0.1, 0.2] shows something.
This means we can compute it. Note that the formula is clear.
""" * 5
        issues = note_quality.heuristic_check("T", content)
        assert any("duplicated" in i.lower() or "mathjax" in i.lower() for i in issues)

    def test_short_note_passes_even_without_headings(self):
        """Short notes don't need headings."""
        content = "A brief observation about something."
        issues = note_quality.heuristic_check("T", content)
        # Should not complain about missing headings on a 1-line note
        assert not any("headings" in i for i in issues)


class TestRevisionTracking:
    def test_initial_count_zero(self):
        assert note_quality.get_revision_count("Test Note") == 0

    def test_increment(self):
        assert note_quality.increment_revision("Test Note") == 1
        assert note_quality.increment_revision("Test Note") == 2
        assert note_quality.get_revision_count("Test Note") == 2

    def test_clear(self):
        note_quality.increment_revision("Test Note")
        note_quality.increment_revision("Test Note")
        note_quality.clear_revision("Test Note")
        assert note_quality.get_revision_count("Test Note") == 0

    def test_title_normalization(self):
        """Titles differing only in whitespace/case should share a counter."""
        note_quality.increment_revision("Test Note")
        assert note_quality.get_revision_count("test note") == 1
        assert note_quality.get_revision_count("  Test   Note  ") == 1

    def test_different_titles_separate_counters(self):
        note_quality.increment_revision("Note A")
        note_quality.increment_revision("Note B")
        assert note_quality.get_revision_count("Note A") == 1
        assert note_quality.get_revision_count("Note B") == 1


class TestReviewNote:
    def test_clean_note_approved_with_heuristics(self):
        """A note that passes heuristics should not even invoke the LLM."""
        title = "Clean Note"
        content = """\
# Clean Note

## Introduction
Here's an explanatory section with $x^2 + y^2$ math. The key insight is
that this passes all heuristic checks. Note that we have headings.

## Example
Consider a simple case. This means we can show something specific.

## Conclusion
Because of this, the note is approved.
"""
        with patch("prax.services.note_quality.llm_review") as mock_llm:
            mock_llm.return_value = {"approved": True, "issues": [], "verdict": "ok"}
            result = note_quality.review_note(title, content)
            assert result["approved"] is True
            assert result["heuristic_issues"] == []

    def test_dirty_note_rejected_by_heuristics(self):
        title = "Dirty Note"
        content = """\
Some text
,
more text with [Image] placeholders
,
and orphan commas.
"""
        with patch("prax.services.note_quality.llm_review") as mock_llm:
            result = note_quality.review_note(title, content)
            assert result["approved"] is False
            assert len(result["heuristic_issues"]) > 0
            # LLM should NOT be called when heuristics already failed
            mock_llm.assert_not_called()

    def test_force_save_after_max_revisions(self):
        title = "Stubborn Note"
        content = "bad content ,"
        # Bump revision count past max
        for _ in range(note_quality.MAX_REVISIONS):
            note_quality.increment_revision(title)
        result = note_quality.review_note(title, content)
        assert result["force_save"] is True

    def test_llm_review_catches_semantic_issues(self):
        title = "Mediocre Note"
        content = """\
# Title

## Section
Some content that passes heuristics because it has headings and an
explanatory transition like "the key insight is". But maybe it's still
shallow.
""" * 5
        with patch("prax.services.note_quality.llm_review") as mock_llm:
            mock_llm.return_value = {
                "approved": False,
                "issues": ["too shallow for deep dive", "missing toy example"],
                "verdict": "needs more depth",
            }
            result = note_quality.review_note(title, content)
            assert result["approved"] is False
            assert len(result["llm_issues"]) == 2


class TestFormatFeedback:
    def test_includes_issues(self):
        review = {
            "approved": False,
            "revision": 0,
            "max_revisions": 3,
            "heuristic_issues": ["orphan comma", "missing headings"],
            "llm_issues": ["too shallow"],
            "verdict": "needs rewrite",
            "force_save": False,
        }
        feedback = note_quality.format_feedback(review)
        assert "orphan comma" in feedback
        assert "missing headings" in feedback
        assert "too shallow" in feedback
        assert "needs rewrite" in feedback

    def test_includes_attempt_count(self):
        review = {
            "approved": False,
            "revision": 1,
            "max_revisions": 3,
            "heuristic_issues": ["issue"],
            "llm_issues": [],
            "verdict": "",
            "force_save": False,
        }
        feedback = note_quality.format_feedback(review)
        assert "1/3" in feedback or "1 /3" in feedback
        assert "2 more attempt" in feedback
