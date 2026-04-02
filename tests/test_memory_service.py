"""Tests for the unified MemoryService facade."""
from unittest.mock import patch

import pytest

from prax.services.memory.models import ConsolidationResult, MemoryResult, STMEntry


@pytest.fixture
def memory_service():
    """Create a MemoryService with memory enabled."""
    with patch("prax.services.memory_service.settings") as mock_settings:
        mock_settings.memory_enabled = True
        import prax.services.memory_service as mod

        mod._instance = None
        svc = mod.get_memory_service()
        yield svc
        mod._instance = None


@pytest.fixture
def disabled_service():
    """Create a MemoryService with memory disabled."""
    with patch("prax.services.memory_service.settings") as mock_settings:
        mock_settings.memory_enabled = False
        import prax.services.memory_service as mod

        mod._instance = None
        svc = mod.get_memory_service()
        yield svc
        mod._instance = None


class TestGracefulDegradation:
    def test_recall_returns_empty_when_disabled(self, disabled_service):
        assert disabled_service.recall("user1", "query") == []

    def test_remember_returns_empty_when_disabled(self, disabled_service):
        assert disabled_service.remember("user1", "content") == ""

    def test_forget_returns_false_when_disabled(self, disabled_service):
        assert disabled_service.forget("user1", "mid") is False

    def test_entity_lookup_returns_none_when_disabled(self, disabled_service):
        assert disabled_service.entity_lookup("user1", "Alice") is None

    def test_consolidate_returns_empty_when_disabled(self, disabled_service):
        result = disabled_service.consolidate("user1")
        assert result.memories_created == 0

    def test_stats_shows_disabled(self, disabled_service):
        stats = disabled_service.stats("user1")
        assert stats["memory_enabled"] is False


class TestSTMOperations:
    """STM works even when LTM is disabled (no infrastructure dependency)."""

    def test_stm_write_and_read(self, disabled_service):
        with patch("prax.services.memory.stm.stm_write") as mock_write, \
             patch("prax.services.memory.stm.stm_read") as mock_read:
            mock_write.return_value = STMEntry(key="tz", content="UTC", importance=0.5)
            mock_read.return_value = [STMEntry(key="tz", content="UTC", importance=0.5)]

            entry = disabled_service.stm_write("user1", "tz", "UTC")
            assert entry.key == "tz"

            entries = disabled_service.stm_read("user1")
            assert len(entries) == 1


class TestRemember:
    def test_remember_stores_memory(self, memory_service):
        with patch("prax.services.memory.embedder.embed_text", return_value=[0.1] * 1536), \
             patch("prax.services.memory.embedder.sparse_encode", return_value={1: 0.5}), \
             patch("prax.services.memory.vector_store.upsert_memory", return_value="mem-123"):
            mid = memory_service.remember("user1", "I prefer dark mode", importance=0.8)
            assert mid == "mem-123"

    def test_remember_handles_errors(self, memory_service):
        with patch("prax.services.memory.embedder.embed_text", side_effect=Exception("API error")):
            mid = memory_service.remember("user1", "test")
            assert mid == ""  # Graceful failure


class TestRecall:
    def test_recall_delegates_to_hybrid_search(self, memory_service):
        with patch("prax.services.memory.retrieval.hybrid_search") as mock_search:
            mock_search.return_value = [
                MemoryResult(
                    memory_id="m1",
                    content="dark mode preference",
                    score=0.95,
                    source="conversation",
                    importance=0.8,
                    created_at="2026-01-01",
                )
            ]
            results = memory_service.recall("user1", "what theme do I prefer?")
            assert len(results) == 1
            assert results[0].content == "dark mode preference"


class TestBuildMemoryContext:
    def test_includes_stm_when_available(self, disabled_service):
        with patch("prax.services.memory.stm.stm_read") as mock_read:
            mock_read.return_value = [
                STMEntry(key="timezone", content="UTC", importance=0.5),
                STMEntry(key="project", content="Alpha", importance=0.8),
            ]
            ctx = disabled_service.build_memory_context("user1", "hello")
            assert "Scratchpad" in ctx
            assert "timezone" in ctx
            assert "Alpha" in ctx

    def test_empty_when_no_memories(self, disabled_service):
        with patch("prax.services.memory.stm.stm_read", return_value=[]):
            ctx = disabled_service.build_memory_context("user1", "hello")
            assert ctx == ""

    def test_includes_ltm_when_enabled(self, memory_service):
        with patch("prax.services.memory.stm.stm_read", return_value=[]), \
             patch("prax.services.memory.retrieval.hybrid_search") as mock_search:
            mock_search.return_value = [
                MemoryResult(
                    memory_id="m1",
                    content="User prefers Python",
                    score=0.9,
                    source="consolidation",
                    importance=0.7,
                    created_at="2026-03-01",
                )
            ]
            ctx = memory_service.build_memory_context("user1", "what language?")
            assert "Relevant Memories" in ctx
            assert "Python" in ctx
