"""Tests for the tool call loop detector."""
from __future__ import annotations

import pytest

from prax.agent.loop_detector import (
    _EXEMPT_TOOLS,
    BLOCK_THRESHOLD,
    REFLECT_THRESHOLD,
    WARN_THRESHOLD,
    check,
    get_loop_stats,
    reset,
)


@pytest.fixture(autouse=True)
def _clean():
    reset()
    yield
    reset()


class TestBasicDetection:
    def test_first_call_is_allowed(self):
        assert check("web_search", {"query": "hello"}) is None

    def test_second_call_is_allowed(self):
        check("web_search", {"query": "hello"})
        assert check("web_search", {"query": "hello"}) is None

    def test_different_args_are_separate(self):
        for _ in range(BLOCK_THRESHOLD):
            check("web_search", {"query": "hello"})
        # Different args should be a fresh count
        assert check("web_search", {"query": "world"}) is None

    def test_different_tools_are_separate(self):
        for _ in range(BLOCK_THRESHOLD):
            check("web_search", {"query": "hello"})
        assert check("background_search", {"query": "hello"}) is None


class TestReflection:
    def test_returns_reflection_at_threshold(self):
        for _ in range(REFLECT_THRESHOLD - 1):
            assert check("web_search", {"query": "x"}) is None
        msg = check("web_search", {"query": "x"})
        assert msg is not None
        assert "same arguments" in msg.lower() or "same args" in msg.lower() or str(REFLECT_THRESHOLD) in msg

    def test_reflection_is_soft(self):
        for _ in range(REFLECT_THRESHOLD - 1):
            check("web_search", {"query": "x"})
        msg = check("web_search", {"query": "x"})
        assert "LOOP DETECTED" not in msg
        assert "WARNING" not in msg


class TestWarning:
    def test_returns_warning_at_threshold(self):
        for _ in range(WARN_THRESHOLD - 1):
            check("web_search", {"query": "x"})
        msg = check("web_search", {"query": "x"})
        assert msg is not None
        assert "WARNING" in msg or "different tool" in msg.lower() or "different approach" in msg.lower()


class TestBlock:
    def test_blocks_at_threshold(self):
        for _ in range(BLOCK_THRESHOLD - 1):
            check("web_search", {"query": "x"})
        msg = check("web_search", {"query": "x"})
        assert msg is not None
        assert "LOOP DETECTED" in msg
        assert "STOP" in msg

    def test_block_includes_count(self):
        for _ in range(BLOCK_THRESHOLD - 1):
            check("web_search", {"query": "x"})
        msg = check("web_search", {"query": "x"})
        assert str(BLOCK_THRESHOLD) in msg


class TestExemptTools:
    def test_exempt_tools_never_blocked(self):
        for exempt_tool in list(_EXEMPT_TOOLS)[:2]:
            reset()
            for _ in range(BLOCK_THRESHOLD + 5):
                result = check(exempt_tool, {"key": "val"})
                assert result is None


class TestReset:
    def test_reset_clears_state(self):
        for _ in range(REFLECT_THRESHOLD):
            check("web_search", {"query": "x"})
        reset()
        # After reset, first call should be clean
        assert check("web_search", {"query": "x"}) is None

    def test_stats_cleared_after_reset(self):
        for _ in range(REFLECT_THRESHOLD):
            check("web_search", {"query": "x"})
        reset()
        stats = get_loop_stats()
        assert stats["unique_signatures"] == 0
        assert stats["total_calls"] == 0


class TestLoopStats:
    def test_empty_stats(self):
        stats = get_loop_stats()
        assert stats["unique_signatures"] == 0
        assert stats["repeated_tools"] == {}
        assert stats["total_calls"] == 0

    def test_stats_track_repeated(self):
        for _ in range(REFLECT_THRESHOLD):
            check("web_search", {"query": "x"})
        stats = get_loop_stats()
        assert stats["unique_signatures"] == 1
        assert "web_search" in stats["repeated_tools"]
        assert stats["repeated_tools"]["web_search"] == REFLECT_THRESHOLD
        assert stats["total_calls"] == REFLECT_THRESHOLD

    def test_stats_exclude_below_threshold(self):
        check("web_search", {"query": "x"})
        check("web_search", {"query": "y"})
        stats = get_loop_stats()
        assert stats["unique_signatures"] == 2
        assert stats["repeated_tools"] == {}  # No repeats above threshold


class TestEscalation:
    def test_escalation_ladder(self):
        """Verify the full escalation: None → reflect → warn → block."""
        results = []
        for _i in range(BLOCK_THRESHOLD + 1):
            msg = check("web_search", {"query": "x"})
            results.append(msg)

        # First calls should be None
        assert results[0] is None
        assert results[1] is None

        # Reflection at REFLECT_THRESHOLD (index REFLECT_THRESHOLD - 1)
        assert results[REFLECT_THRESHOLD - 1] is not None
        assert "LOOP DETECTED" not in results[REFLECT_THRESHOLD - 1]

        # Warning at WARN_THRESHOLD
        assert results[WARN_THRESHOLD - 1] is not None
        assert "WARNING" in results[WARN_THRESHOLD - 1] or "different" in results[WARN_THRESHOLD - 1].lower()

        # Block at BLOCK_THRESHOLD
        assert results[BLOCK_THRESHOLD - 1] is not None
        assert "LOOP DETECTED" in results[BLOCK_THRESHOLD - 1]
