"""Tests for hybrid retrieval engine (RRF fusion)."""

from prax.services.memory import retrieval as _retrieval
from prax.services.memory.models import MemoryResult
from prax.services.memory.retrieval import (
    _expand_queries,
    _extract_key_terms,
    _rerank,
    rrf_fuse,
)


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


class TestQueryExpansion:
    def test_n_le_1_returns_original_only(self):
        assert _expand_queries("hello", 1) == ["hello"]

    def test_expands_and_dedupes(self, monkeypatch):
        monkeypatch.setattr(
            _retrieval, "_llm_complete",
            lambda prompt: "1. what is my dog's name\n- the name of my pet dog\nhello world\n",
        )
        out = _expand_queries("hello world", 3)
        # Original first, then up to n-1 cleaned variants, original-dup dropped.
        assert out[0] == "hello world"
        assert "what is my dog's name" in out
        assert "the name of my pet dog" in out
        assert len(out) <= 3

    def test_degrades_to_original_on_empty(self, monkeypatch):
        monkeypatch.setattr(_retrieval, "_llm_complete", lambda prompt: "")
        assert _expand_queries("hi there", 3) == ["hi there"]


class TestRerank:
    def test_reorders_by_relevance(self, monkeypatch):
        cands = [_mr("a"), _mr("b"), _mr("c")]
        # Judge says c is most relevant, then a, then b.
        monkeypatch.setattr(
            _retrieval, "_llm_complete",
            lambda prompt: "0:30\n1:10\n2:95\n",
        )
        out = _rerank("q", cands, max_candidates=20)
        assert [r.memory_id for r in out] == ["c", "a", "b"]

    def test_preserves_tail_beyond_max_candidates(self, monkeypatch):
        cands = [_mr("a"), _mr("b"), _mr("c")]
        monkeypatch.setattr(_retrieval, "_llm_complete", lambda prompt: "0:10\n1:90\n")
        out = _rerank("q", cands, max_candidates=2)
        # head [a,b] reranked → [b,a]; tail [c] untouched at the end.
        assert [r.memory_id for r in out] == ["b", "a", "c"]

    def test_degrades_to_input_order_on_unparseable(self, monkeypatch):
        cands = [_mr("a"), _mr("b")]
        monkeypatch.setattr(_retrieval, "_llm_complete", lambda prompt: "garbage with no scores")
        out = _rerank("q", cands, max_candidates=20)
        assert [r.memory_id for r in out] == ["a", "b"]


class TestDenseArmExpansionFlag:
    def test_disabled_calls_search_dense_once(self, monkeypatch):
        from prax.settings import settings
        monkeypatch.setattr(settings, "retrieval_query_expansion_enabled", False)
        calls = {"n": 0}
        monkeypatch.setattr("prax.services.memory.embedder.embed_text", lambda q: [0.0])
        def _search_dense(uid, vec, top_k, min_importance=0.0):
            calls["n"] += 1
            return [_mr("a")]
        monkeypatch.setattr("prax.services.memory.vector_store.search_dense", _search_dense)
        out = _retrieval._dense_arm("u", "query", top_k=5, min_importance=0.0)
        assert calls["n"] == 1
        assert [r.memory_id for r in out] == ["a"]
