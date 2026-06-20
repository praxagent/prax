"""Tests for hybrid (vector + keyword) knowledge-concept retrieval."""
from __future__ import annotations

from prax.services.memory import knowledge_graph as kg
from prax.services.memory import knowledge_vectors as kv

# --------------------------------------------------------------------------- #
# RRF fusion helper
# --------------------------------------------------------------------------- #

def test_rrf_fuses_two_id_lists():
    out = kv._rrf([["a", "b", "c"], ["c", "a"]])
    # 'a': 1/61 + 1/62 ; 'c': 1/63 + 1/61 ; 'a' edges out 'c'.
    order = [cid for cid, _ in out]
    assert order[0] in ("a", "c")
    assert set(order) == {"a", "b", "c"}


def test_available_respects_flags(monkeypatch):
    monkeypatch.setattr(kv.settings, "memory_enabled", True)
    monkeypatch.setattr(kv.settings, "knowledge_hybrid_enabled", True)
    assert kv.available() is True
    monkeypatch.setattr(kv.settings, "knowledge_hybrid_enabled", False)
    assert kv.available() is False


def test_search_returns_empty_when_unavailable(monkeypatch):
    monkeypatch.setattr(kv.settings, "knowledge_hybrid_enabled", False)
    assert kv.search("u1", "anything") == []


# --------------------------------------------------------------------------- #
# Hybrid search_knowledge: fusion + graceful fallback
# --------------------------------------------------------------------------- #

def test_search_falls_back_to_keyword_when_no_vectors(monkeypatch):
    monkeypatch.setattr(kg, "ensure_kg_indexes", lambda: None)
    kw = [{"id": "k1", "name": "alpha"}, {"id": "k2", "name": "beta"}]
    monkeypatch.setattr(kg, "_keyword_search", lambda *a, **k: kw)
    monkeypatch.setattr("prax.services.memory.knowledge_vectors.search", lambda *a, **k: [])

    out = kg.search_knowledge("u1", "alpha")
    assert out == kw  # identical to the keyword arm


def test_search_fuses_vector_and_keyword(monkeypatch):
    monkeypatch.setattr(kg, "ensure_kg_indexes", lambda: None)
    # Keyword finds k1 only; vector ranks v9 (semantic-only) above k1.
    kw = [{"id": "k1", "name": "transformer"}]
    monkeypatch.setattr(kg, "_keyword_search", lambda *a, **k: kw)
    monkeypatch.setattr(
        "prax.services.memory.knowledge_vectors.search",
        lambda *a, **k: [("v9", 0.9), ("k1", 0.5)],
    )
    # Hydrate the vector-only hit.
    monkeypatch.setattr(
        kg, "_get_concepts_by_ids",
        lambda uid, ids: {"v9": {"id": "v9", "name": "self-attention"}},
    )

    out = kg.search_knowledge("u1", "attention mechanism")
    ids = [r["id"] for r in out]
    assert set(ids) == {"k1", "v9"}        # union of both arms
    assert "v9" in ids                      # semantic-only concept surfaced


def test_search_limit_respected(monkeypatch):
    monkeypatch.setattr(kg, "ensure_kg_indexes", lambda: None)
    kw = [{"id": f"k{i}", "name": str(i)} for i in range(5)]
    monkeypatch.setattr(kg, "_keyword_search", lambda *a, **k: kw)
    monkeypatch.setattr(
        "prax.services.memory.knowledge_vectors.search",
        lambda *a, **k: [(f"k{i}", 1.0 / (i + 1)) for i in range(5)],
    )
    out = kg.search_knowledge("u1", "q", limit=3)
    assert len(out) == 3


def test_reindex_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(kv.settings, "knowledge_hybrid_enabled", False)
    assert kg.reindex_user_concepts("u1") == 0
