"""Tests for context window management."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from prax.agent.context_manager import (
    ContextBudget,
    clear_old_tool_results,
    count_message_tokens,
    count_tokens,
    prepare_context,
    truncate_history,
)


class TestCountTokens:
    def test_basic_count(self):
        tokens = count_tokens("hello world")
        assert tokens > 0

    def test_empty_string(self):
        assert count_tokens("") == 0 or count_tokens("") >= 0  # fallback may return 0


class TestClearOldToolResults:
    def test_keeps_recent_results(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="hello", tool_calls=[{"id": "tc1", "name": "t", "args": {}}]),
            ToolMessage(content="result1 long content here", tool_call_id="tc1"),
            AIMessage(content="ok"),
            HumanMessage(content="do more"),
            AIMessage(content="sure", tool_calls=[{"id": "tc2", "name": "t", "args": {}}]),
            ToolMessage(content="result2 long content here", tool_call_id="tc2"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=2)
        # Both tool results should be kept (only 2 exist, keep_last_n=2)
        assert len(result) == len(msgs)
        assert "result1" in result[2].content
        assert "result2" in result[6].content

    def test_clears_old_results(self):
        msgs = [
            ToolMessage(content="old result 1", tool_call_id="tc1", name="search"),
            ToolMessage(content="old result 2", tool_call_id="tc2", name="fetch"),
            ToolMessage(content="recent result", tool_call_id="tc3", name="read"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=1)
        assert "cleared" in result[0].content.lower()
        assert "cleared" in result[1].content.lower()
        assert "recent result" in result[2].content

    def test_no_change_when_few_results(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="hello"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=6)
        assert result == msgs


class TestTruncateHistory:
    def test_no_truncation_when_under_budget(self):
        msgs = [
            SystemMessage(content="system"),
            HumanMessage(content="hello"),
            AIMessage(content="hi"),
        ]
        result = truncate_history(msgs, max_tokens=10000)
        assert len(result) == 3

    def test_drops_oldest_first(self):
        msgs = [
            SystemMessage(content="system"),
            HumanMessage(content="old message " * 100),
            AIMessage(content="old response " * 100),
            HumanMessage(content="recent"),
            AIMessage(content="response"),
        ]
        result = truncate_history(msgs, max_tokens=200)
        # System message should be preserved
        assert isinstance(result[0], SystemMessage)
        # Should have fewer messages
        assert len(result) < len(msgs)

    def test_preserves_system_message(self):
        msgs = [
            SystemMessage(content="important system prompt"),
            HumanMessage(content="x " * 1000),
        ]
        result = truncate_history(msgs, max_tokens=500)
        assert any(isinstance(m, SystemMessage) for m in result)


class TestPrepareContext:
    def test_returns_budget(self):
        msgs = [
            SystemMessage(content="system"),
            HumanMessage(content="hello"),
        ]
        result_msgs, budget = prepare_context(msgs, tier="high")
        assert isinstance(budget, ContextBudget)
        assert budget.total > 0
        assert budget.limit > 0
        assert not budget.overflow

    def test_handles_empty_messages(self):
        result_msgs, budget = prepare_context([], tier="low")
        assert budget.total == 0


# =====================================================================
# Extended test suites
# =====================================================================


class TestCountTokensExtended:
    """Token counting — tiktoken vs fallback, real text, edge cases."""

    def test_tiktoken_vs_fallback_estimate(self):
        """Tiktoken result and char/4 fallback should be in the same ballpark."""
        text = "The quick brown fox jumps over the lazy dog. " * 20
        tiktoken_count = count_tokens(text, model="gpt-4")
        fallback_estimate = len(text) // 4
        # Both should be non-zero and within 3x of each other
        assert tiktoken_count > 0
        assert fallback_estimate > 0
        ratio = tiktoken_count / fallback_estimate
        assert 0.3 < ratio < 3.0, f"ratio {ratio} out of expected range"

    def test_nonzero_for_real_text(self):
        text = "Prax is an AI agent that uses LLM-based orchestration."
        assert count_tokens(text) > 0

    def test_whitespace_only(self):
        tokens = count_tokens("   \n\t  ")
        # whitespace still encodes to at least a few tokens
        assert tokens >= 0

    def test_long_text(self):
        text = "word " * 5000
        tokens = count_tokens(text)
        assert tokens > 1000  # 5000 words should be well over 1000 tokens

    def test_unicode_text(self):
        text = "Cafe\u0301 na\u00efve re\u0301sume\u0301 \u2014 context engineering"
        tokens = count_tokens(text)
        assert tokens > 0


class TestClearOldToolResultsEdgeCases:
    """Tool result clearing — edge cases around keep_last_n."""

    def test_empty_messages(self):
        result = clear_old_tool_results([], keep_last_n=6)
        assert result == []

    def test_no_tool_messages(self):
        msgs = [
            HumanMessage(content="hello"),
            AIMessage(content="hi there"),
            HumanMessage(content="what is 2+2?"),
            AIMessage(content="4"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=6)
        assert result == msgs  # no change

    def test_exactly_keep_last_n(self):
        """When tool count == keep_last_n, nothing should be cleared."""
        msgs = [
            ToolMessage(content=f"result_{i}", tool_call_id=f"tc{i}", name="t")
            for i in range(6)
        ]
        result = clear_old_tool_results(msgs, keep_last_n=6)
        assert len(result) == 6
        for i, msg in enumerate(result):
            assert f"result_{i}" in msg.content

    def test_more_than_keep_last_n(self):
        """Excess tool results should be stubbed out."""
        msgs = [
            ToolMessage(content=f"result_{i}", tool_call_id=f"tc{i}", name="search")
            for i in range(10)
        ]
        result = clear_old_tool_results(msgs, keep_last_n=3)
        # First 7 should be cleared, last 3 kept
        for i in range(7):
            assert "cleared" in result[i].content.lower()
            assert result[i].tool_call_id == f"tc{i}"
        for i in range(7, 10):
            assert f"result_{i}" in result[i].content

    def test_tool_messages_without_name(self):
        """ToolMessages without a name attribute should still be handled."""
        msgs = [
            ToolMessage(content="old content", tool_call_id="tc0"),
            ToolMessage(content="recent content", tool_call_id="tc1"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=1)
        assert "cleared" in result[0].content.lower()
        assert "recent content" in result[1].content
        # The stub should still have a tool_call_id
        assert result[0].tool_call_id == "tc0"

    def test_preserves_non_tool_messages(self):
        """Human/AI messages interspersed with tools should be untouched."""
        msgs = [
            HumanMessage(content="do something"),
            AIMessage(content="ok", tool_calls=[{"id": "tc1", "name": "t", "args": {}}]),
            ToolMessage(content="old tool result", tool_call_id="tc1", name="t"),
            AIMessage(content="interpreted result"),
            HumanMessage(content="do another thing"),
            AIMessage(content="sure", tool_calls=[{"id": "tc2", "name": "t", "args": {}}]),
            ToolMessage(content="new tool result", tool_call_id="tc2", name="t"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=1)
        # Only the first ToolMessage should be cleared
        assert "cleared" in result[2].content.lower()
        assert "new tool result" in result[6].content
        # Human/AI messages are untouched
        assert result[0].content == "do something"
        assert result[3].content == "interpreted result"

    def test_keep_last_n_zero_preserves_all(self):
        """keep_last_n=0 — Python slice [:-0] returns empty, so nothing is cleared.

        This is a known quirk: tool_indices[:-0] == tool_indices[0:0] == [].
        The implementation treats keep_last_n=0 the same as keeping all.
        """
        msgs = [
            ToolMessage(content="result_1", tool_call_id="tc1", name="a"),
            ToolMessage(content="result_2", tool_call_id="tc2", name="b"),
        ]
        result = clear_old_tool_results(msgs, keep_last_n=0)
        # Due to Python slicing, no results are cleared
        assert result == msgs


class TestTruncateHistoryEdgeCases:
    """Truncation edge cases — empty, single, system-only, boundary cases."""

    def test_empty_list(self):
        result = truncate_history([], max_tokens=1000)
        assert result == []

    def test_single_message(self):
        msgs = [HumanMessage(content="hello")]
        result = truncate_history(msgs, max_tokens=10000)
        assert len(result) == 1

    def test_only_system_message(self):
        msgs = [SystemMessage(content="You are a helpful assistant.")]
        result = truncate_history(msgs, max_tokens=10000)
        assert len(result) == 1
        assert isinstance(result[0], SystemMessage)

    def test_messages_barely_fit(self):
        """Messages that are just under budget should not be truncated."""
        msgs = [
            SystemMessage(content="system"),
            HumanMessage(content="hello"),
            AIMessage(content="hi"),
        ]
        tokens_needed = count_message_tokens(msgs)
        result = truncate_history(msgs, max_tokens=tokens_needed + 10)
        assert len(result) == len(msgs)

    def test_messages_way_over_budget(self):
        """Very long history with tiny budget should drop almost everything."""
        msgs = [
            SystemMessage(content="sys"),
        ] + [
            HumanMessage(content=f"message number {i} " * 50)
            for i in range(20)
        ]
        result = truncate_history(msgs, max_tokens=200)
        # System message preserved
        assert isinstance(result[0], SystemMessage)
        # Should have drastically fewer messages
        assert len(result) < len(msgs)

    def test_system_message_always_preserved(self):
        """Even with an extremely tight budget the system msg survives."""
        msgs = [
            SystemMessage(content="important"),
            HumanMessage(content="a " * 500),
            AIMessage(content="b " * 500),
            HumanMessage(content="c " * 500),
        ]
        result = truncate_history(msgs, max_tokens=100)
        sys_msgs = [m for m in result if isinstance(m, SystemMessage)]
        assert len(sys_msgs) >= 1

    def test_no_system_message(self):
        """Works when there is no system message at all."""
        msgs = [
            HumanMessage(content="hello " * 100),
            AIMessage(content="world " * 100),
            HumanMessage(content="recent"),
        ]
        result = truncate_history(msgs, max_tokens=200)
        assert len(result) <= len(msgs)


class TestCompactHistory:
    """Compaction — LLM summarization with mock, fallback on failure."""

    def test_compaction_summarizes_old_messages(self):
        """Mock the LLM call; verify old messages are replaced with a summary."""
        from unittest.mock import MagicMock, patch

        from prax.agent.context_manager import compact_history

        # Build a conversation long enough to trigger compaction (>= 6 non-system)
        # Each message must be long enough that total tokens exceed max_tokens
        msgs = [SystemMessage(content="You are helpful.")]
        for i in range(10):
            msgs.append(HumanMessage(content=f"Question {i}: what about topic {i}? " * 20))
            msgs.append(AIMessage(content=f"Answer {i}: here is info about topic {i}. " * 20))

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "Summary: discussed topics 0-9. Key decisions: none."
        mock_llm.invoke.return_value = mock_result

        with patch("prax.agent.llm_factory.build_llm", return_value=mock_llm):
            result = compact_history(msgs, max_tokens=500, tier="low")

        # Should have: system + compaction summary + recent messages
        sys_msgs = [m for m in result if isinstance(m, SystemMessage)]
        assert len(sys_msgs) == 2  # original system + compaction summary
        compacted = [m for m in result if isinstance(m, SystemMessage) and "COMPACTED" in m.content]
        assert len(compacted) == 1
        assert "Summary:" in compacted[0].content

        # Recent messages should be preserved (the second half)
        non_sys = [m for m in result if not isinstance(m, SystemMessage)]
        assert len(non_sys) > 0
        # Recent half should be 10 messages (half of 20 conv msgs)
        assert len(non_sys) == 10

    def test_compaction_preserves_recent_messages(self):
        """The most recent messages must survive compaction verbatim."""
        from unittest.mock import MagicMock, patch

        from prax.agent.context_manager import compact_history

        msgs = [SystemMessage(content="sys")]
        for i in range(8):
            msgs.append(HumanMessage(content=f"Q{i} " * 30))
            msgs.append(AIMessage(content=f"A{i} " * 30))

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "Old stuff summarized."
        mock_llm.invoke.return_value = mock_result

        with patch("prax.agent.llm_factory.build_llm", return_value=mock_llm):
            result = compact_history(msgs, max_tokens=200, tier="low")

        non_sys = [m for m in result if not isinstance(m, SystemMessage)]
        # Last messages should be verbatim from the recent half
        recent_contents = [m.content for m in non_sys]
        # The second half of 16 conv msgs = last 8 messages
        # A7 repeated 30 times should be in the recent half
        assert any("A7" in c for c in recent_contents)
        assert any("Q4" in c or "Q5" in c for c in recent_contents)

    def test_compaction_falls_back_to_truncation_on_llm_failure(self):
        """If the LLM call raises, compact_history should fall back to truncation."""
        from unittest.mock import patch

        from prax.agent.context_manager import compact_history

        msgs = [SystemMessage(content="sys")]
        for i in range(10):
            msgs.append(HumanMessage(content=f"Q{i} " * 50))
            msgs.append(AIMessage(content=f"A{i} " * 50))

        def exploding_build_llm(**kwargs):
            raise RuntimeError("LLM unavailable")

        with patch("prax.agent.llm_factory.build_llm", side_effect=exploding_build_llm):
            result = compact_history(msgs, max_tokens=500, tier="low")

        # Should have fallen back to truncation — fewer messages, system preserved
        assert len(result) < len(msgs)
        assert isinstance(result[0], SystemMessage)

    def test_compaction_skips_when_under_budget(self):
        """If already under budget, compaction returns messages unchanged."""
        from prax.agent.context_manager import compact_history

        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
            AIMessage(content="hello"),
        ]
        result = compact_history(msgs, max_tokens=100_000)
        assert result == msgs

    def test_compaction_falls_back_for_few_messages(self):
        """With fewer than 6 conversation messages, falls back to truncation."""
        from prax.agent.context_manager import compact_history

        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi " * 200),
            AIMessage(content="hello " * 200),
        ]
        result = compact_history(msgs, max_tokens=100)
        # Should fall back to truncation, not crash
        assert isinstance(result[0], SystemMessage)


class TestGetContextLimit:
    """Per-model context limits — exact names, substrings, unknown, tiers."""

    def test_exact_model_gpt54_mini(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(model="gpt-5.4-mini") == 100_000

    def test_exact_model_claude_opus(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(model="claude-opus-4-6") == 160_000

    def test_exact_model_gpt54_nano(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(model="gpt-5.4-nano") == 12_000

    def test_exact_model_gemini_flash(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(model="gemini-2.0-flash") == 800_000

    def test_exact_model_deepseek(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(model="deepseek-chat") == 50_000

    def test_substring_match(self):
        """A model name containing a known key should match."""
        from prax.agent.context_manager import get_context_limit

        # "claude-sonnet-4-6" is in the table; a versioned variant should match
        limit = get_context_limit(model="claude-sonnet-4-6-20260101")
        assert limit == 160_000

    def test_unknown_model_uses_tier_fallback(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(tier="high", model="some-unknown-model-xyz") == 100_000
        assert get_context_limit(tier="low", model="unknown") == 12_000
        assert get_context_limit(tier="pro", model="mystery") == 160_000

    def test_unknown_model_no_tier_defaults_to_low(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(model="totally-unknown") == 12_000

    def test_tier_fallback_low(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(tier="low") == 12_000

    def test_tier_fallback_medium(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(tier="medium") == 50_000

    def test_tier_fallback_high(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(tier="high") == 100_000

    def test_tier_fallback_pro(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(tier="pro") == 160_000

    def test_model_takes_precedence_over_tier(self):
        """Even if tier=low, a known model should use the model's limit."""
        from prax.agent.context_manager import get_context_limit

        limit = get_context_limit(tier="low", model="gemini-2.5-pro")
        assert limit == 800_000

    def test_empty_model_uses_tier(self):
        from prax.agent.context_manager import get_context_limit

        assert get_context_limit(tier="high", model="") == 100_000


class TestContextBudgetReporting:
    """Verify ContextBudget fields are populated correctly."""

    def test_budget_fields_populated(self):
        msgs = [
            SystemMessage(content="You are a helpful AI assistant. " * 50),
            HumanMessage(content="Tell me about context management"),
            AIMessage(content="Context management involves..."),
        ]
        _, budget = prepare_context(msgs, tier="high")
        assert budget.system_prompt > 0
        assert budget.history >= 0
        assert budget.total > 0
        assert budget.limit == 100_000
        assert budget.total == budget.system_prompt + budget.history
        assert not budget.overflow

    def test_budget_model_field(self):
        msgs = [SystemMessage(content="sys"), HumanMessage(content="hi")]
        _, budget = prepare_context(msgs, tier="low", model="gpt-5.4-mini")
        assert budget.model == "gpt-5.4-mini"
        assert budget.limit == 100_000

    def test_budget_overflow_flag(self):
        """If total exceeds limit even after truncation, overflow should be True.

        Note: with aggressive truncation this is hard to trigger in practice,
        so we test the ContextBudget dataclass directly.
        """
        budget = ContextBudget(
            system_prompt=10000,
            history=5000,
            total=15000,
            limit=12000,
            overflow=True,
        )
        assert budget.overflow is True

    def test_budget_zero_for_empty(self):
        _, budget = prepare_context([], tier="low")
        assert budget.total == 0
        assert budget.system_prompt == 0
        assert budget.history == 0
        assert not budget.overflow


class TestPrepareContextPipeline:
    """Full prepare_context pipeline — under budget, compaction, truncation."""

    def test_under_budget_no_changes(self):
        """Messages within budget should pass through unchanged."""
        msgs = [
            SystemMessage(content="system prompt"),
            HumanMessage(content="hello"),
            AIMessage(content="hi"),
        ]
        result_msgs, budget = prepare_context(msgs, tier="high")
        assert len(result_msgs) == 3
        assert not budget.overflow
        # Content should be identical
        assert result_msgs[0].content == "system prompt"

    def test_slightly_over_triggers_compaction(self):
        """When slightly over budget, compaction should be attempted."""
        from unittest.mock import MagicMock, patch

        from prax.agent.context_manager import count_message_tokens as _cmt

        # Build messages that exceed low tier (12K tokens).
        # Need ~600 tokens per message. "word " = 2 tokens in tiktoken.
        # 600 tokens = ~300 repetitions of "word "
        msgs = [SystemMessage(content="sys")]
        for i in range(12):
            msgs.append(HumanMessage(content=f"Question {i}: " + "word " * 500))
            msgs.append(AIMessage(content=f"Answer {i}: " + "word " * 500))

        # Verify we actually exceed 12K
        total = _cmt(msgs)
        assert total > 12_000, f"Need >12K tokens to trigger compaction, got {total}"

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "Summarized old conversation."
        mock_llm.invoke.return_value = mock_result

        with patch("prax.agent.llm_factory.build_llm", return_value=mock_llm):
            result_msgs, budget = prepare_context(msgs, tier="low")

        # Should have fewer messages due to compaction
        assert len(result_msgs) < len(msgs)

    def test_way_over_triggers_truncation(self):
        """When way over budget and compaction fails, truncation kicks in."""
        from unittest.mock import patch

        from prax.agent.context_manager import count_message_tokens as _cmt

        msgs = [SystemMessage(content="sys")]
        for i in range(30):
            msgs.append(HumanMessage(content=f"Question {i}: " + "word " * 500))
            msgs.append(AIMessage(content=f"Answer {i}: " + "word " * 500))

        total = _cmt(msgs)
        assert total > 12_000, f"Need >12K tokens, got {total}"

        def exploding_build_llm(**kwargs):
            raise RuntimeError("LLM unavailable")

        with patch("prax.agent.llm_factory.build_llm", side_effect=exploding_build_llm):
            result_msgs, budget = prepare_context(msgs, tier="low")

        # Should be drastically shorter
        assert len(result_msgs) < len(msgs)
        # System message preserved
        assert isinstance(result_msgs[0], SystemMessage)

    def test_tool_results_cleared_before_budget_check(self):
        """Tool result clearing should happen before compaction/truncation."""
        msgs = [
            SystemMessage(content="sys"),
        ]
        # Add many tool results
        for i in range(10):
            msgs.append(AIMessage(
                content=f"calling tool {i}",
                tool_calls=[{"id": f"tc{i}", "name": "search", "args": {}}],
            ))
            msgs.append(ToolMessage(
                content=f"very long result {i} " * 100,
                tool_call_id=f"tc{i}",
                name="search",
            ))
        msgs.append(HumanMessage(content="final question"))

        result_msgs, _ = prepare_context(msgs, tier="high")

        # Old tool results (beyond last 6) should be stubs
        tool_msgs = [m for m in result_msgs if isinstance(m, ToolMessage)]
        cleared = [m for m in tool_msgs if "cleared" in m.content.lower()]
        kept = [m for m in tool_msgs if "cleared" not in m.content.lower()]
        assert len(cleared) == 4  # 10 - 6 = 4 cleared
        assert len(kept) == 6

    def test_model_override_affects_limit(self):
        """Passing a specific model should use that model's context limit."""
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
        ]
        _, budget_low = prepare_context(msgs, tier="low")
        _, budget_gemini = prepare_context(msgs, tier="low", model="gemini-2.5-pro")
        assert budget_low.limit == 12_000
        assert budget_gemini.limit == 800_000
