"""Tests for hybrid retrieval engine (RRF fusion)."""
import pytest

from prax.services.memory.models import MemoryResult
from prax.services.memory.retrieval import _extract_key_terms, rrf_fuse


def _mr(mid: str, score: float = 1.0, content: str = "", importance: float = 0.5) -> MemoryResult:
    """Helper to create test MemoryResult instances."""
    return MemoryResult(
        memory_id=mid,
        content=content or f"memory {mid}",
        score=score,
        source="test",
        importance=importance,
        created_at="2026-01-01T00:00:00Z",
    )


class TestRRFFusion:
    def test_single_list(self):
        results = rrf_fuse([[_mr("a", 0.9), _mr("b", 0.8), _mr("c", 0.7)]])
        assert len(results) == 3
        assert results[0].memory_id == "a"
        assert results[1].memory_id == "b"
        assert results[2].memory_id == "c"

    def test_two_lists_same_order(self):
        list1 = [_mr("a", 0.9), _mr("b", 0.8)]
        list2 = [_mr("a", 0.95), _mr("b", 0.85)]
        results = rrf_fuse([list1, list2])
        # 'a' appears first in both lists → highest RRF score
        assert results[0].memory_id == "a"
        assert results[1].memory_id == "b"
        # Score should be sum of reciprocal ranks from both lists
        # a: 1/(60+1) + 1/(60+1) = 2/61
        assert abs(results[0].score - 2 / 61) < 0.001

    def test_two_lists_different_order(self):
        list1 = [_mr("a"), _mr("b"), _mr("c")]
        list2 = [_mr("c"), _mr("a"), _mr("b")]
        results = rrf_fuse([list1, list2])
        # 'a': rank 0 in list1 + rank 1 in list2 → 1/61 + 1/62
        # 'c': rank 2 in list1 + rank 0 in list2 → 1/63 + 1/61
        # 'b': rank 1 in list1 + rank 2 in list2 → 1/62 + 1/63
        scores = {r.memory_id: r.score for r in results}
        assert scores["a"] > scores["b"]

    def test_unique_items_across_lists(self):
        list1 = [_mr("a")]
        list2 = [_mr("b")]
        results = rrf_fuse([list1, list2])
        assert len(results) == 2
        # Both have same rank (0) in their respective lists
        assert abs(results[0].score - results[1].score) < 0.001

    def test_empty_lists(self):
        results = rrf_fuse([[], []])
        assert results == []

    def test_preserves_best_metadata(self):
        list1 = [_mr("a", 0.5, content="short")]
        list2 = [_mr("a", 0.9, content="detailed")]
        results = rrf_fuse([list1, list2])
        # Should keep the result with higher original score
        assert results[0].content == "detailed"

    def test_three_way_fusion(self):
        list1 = [_mr("a"), _mr("b")]
        list2 = [_mr("b"), _mr("c")]
        list3 = [_mr("a"), _mr("c")]
        results = rrf_fuse([list1, list2, list3])
        scores = {r.memory_id: r.score for r in results}
        # 'a' appears in list1(rank0) + list3(rank0) → 2/61
        # 'b' appears in list1(rank1) + list2(rank0) → 1/62 + 1/61
        # 'c' appears in list2(rank1) + list3(rank1) → 2/62
        assert scores["a"] > scores["c"]


class TestKeyTermExtraction:
    def test_basic_extraction(self):
        terms = _extract_key_terms("What do you know about quantum computing?")
        assert "quantum" in terms
        assert "computing" in terms

    def test_capitalised_words(self):
        terms = _extract_key_terms("Tell me about Alice and Bob")
        assert "Alice" in terms
        assert "Bob" in terms

    def test_quoted_phrases(self):
        terms = _extract_key_terms('What is "machine learning"?')
        assert "machine learning" in terms

    def test_filters_stop_words(self):
        terms = _extract_key_terms("what do you remember about this")
        # All common stop words should be filtered
        assert "what" not in terms
        assert "you" not in terms
        assert "remember" not in terms

    def test_empty_query(self):
        assert _extract_key_terms("") == []

    def test_deduplication(self):
        terms = _extract_key_terms("Python Python Python programming")
        assert terms.count("Python") == 1


class TestSparseEncoding:
    def test_basic_encoding(self):
        from prax.services.memory.embedder import sparse_encode

        result = sparse_encode("hello world programming")
        assert isinstance(result, dict)
        assert len(result) > 0
        # All values should be positive
        assert all(v > 0 for v in result.values())

    def test_empty_text(self):
        from prax.services.memory.embedder import sparse_encode

        assert sparse_encode("") == {}

    def test_stop_words_filtered(self):
        from prax.services.memory.embedder import sparse_encode

        result_with = sparse_encode("the quick brown fox")
        result_without = sparse_encode("quick brown fox")
        # "the" is a stop word, so results should be similar
        # (same tokens after filtering)
        assert len(result_with) == len(result_without)
