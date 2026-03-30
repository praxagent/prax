"""Tests for prax.agent.difficulty — task difficulty estimation."""
from __future__ import annotations

from prax.agent.difficulty import (
    EASY,
    HARD,
    MODERATE,
    difficulty_context_for_prompt,
    estimate_difficulty,
    recommended_tier,
)


class TestEstimateDifficulty:
    def test_empty_message_is_easy(self):
        assert estimate_difficulty("") == EASY
        assert estimate_difficulty("   ") == EASY

    def test_simple_greeting_is_easy(self):
        assert estimate_difficulty("hello") == EASY
        assert estimate_difficulty("hi there") == EASY

    def test_simple_question_is_easy(self):
        assert estimate_difficulty("What time is it?") == EASY
        assert estimate_difficulty("What is Python?") == EASY

    def test_save_file_is_easy(self):
        assert estimate_difficulty("Save a file called todo.md to my workspace") == EASY

    def test_research_request_is_hard(self):
        assert estimate_difficulty(
            "Research the latest developments in quantum computing and "
            "compare the approaches of IBM, Google, and Microsoft"
        ) == HARD

    def test_multi_step_is_moderate_or_hard(self):
        result = estimate_difficulty(
            "First search for the paper, then download the PDF, "
            "and create a summary note"
        )
        assert result in (MODERATE, HARD)

    def test_complex_analysis_is_hard(self):
        assert estimate_difficulty(
            "Analyze the performance characteristics of different sorting "
            "algorithms and synthesize a comprehensive comparison with "
            "detailed time and space complexity analysis for each"
        ) == HARD

    def test_url_adds_difficulty(self):
        # Without URL
        base = estimate_difficulty("Get info about Python")
        # With URL
        with_url = estimate_difficulty(
            "Get info about Python from https://docs.python.org and "
            "https://wiki.python.org"
        )
        assert with_url != EASY or base == EASY  # URL shouldn't decrease difficulty

    def test_short_factual_is_easy(self):
        assert estimate_difficulty("Thanks!") == EASY
        assert estimate_difficulty("List my todos") == EASY

    def test_coding_request_is_moderate_or_hard(self):
        result = estimate_difficulty("Write code to implement a binary search tree")
        assert result in (MODERATE, HARD)

    def test_arxiv_is_moderate_or_hard(self):
        result = estimate_difficulty(
            "Download the paper from arxiv about transformer architecture"
        )
        assert result in (MODERATE, HARD)


class TestRecommendedTier:
    def test_easy_returns_low(self):
        assert recommended_tier("What time is it?") == "low"

    def test_hard_returns_medium(self):
        assert recommended_tier(
            "Research and compare nuclear fission and fusion, "
            "synthesize a comprehensive analysis"
        ) == "medium"


class TestDifficultyContextForPrompt:
    def test_easy_mentions_straightforward(self):
        ctx = difficulty_context_for_prompt("hello")
        assert "EASY" in ctx
        assert "straightforward" in ctx

    def test_hard_mentions_complex(self):
        ctx = difficulty_context_for_prompt(
            "Research, analyze, and synthesize a deep-dive comparison"
        )
        assert "HARD" in ctx
        assert "complex" in ctx.lower() or "plan" in ctx.lower()
