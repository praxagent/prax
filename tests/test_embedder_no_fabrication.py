"""embed_texts must RAISE when no provider works — never fabricate vectors.

The old last resort returned all-zero vectors with a success status. On the
live box (found 2026-08-30) BOTH providers were broken for weeks — the
configured provider pointed at an Ollama that was never installed, and
fastembed was absent from the venv — so every memory write took that path.
The store stayed empty only because a separate Qdrant error also failed the
write; had it succeeded, memory would have filled with entries that CLAIM to
be stored and can never be found. Worse than empty.
"""
from __future__ import annotations

import pytest

import prax.services.memory.embedder as emb
from prax.services.memory.embedder import EmbeddingUnavailableError, embed_texts


@pytest.fixture(autouse=True)
def _clean_cache():
    emb._embed_cache.clear()
    yield
    emb._embed_cache.clear()


def test_raises_when_primary_and_fallback_both_fail(monkeypatch):
    monkeypatch.setattr(emb, "_embed_openai",
                        lambda t, m: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(emb, "_embed_fastembed",
                        lambda t: (_ for _ in ()).throw(ModuleNotFoundError("fastembed")))
    monkeypatch.setattr(emb.settings, "embedding_provider", "openai", raising=False)
    with pytest.raises(EmbeddingUnavailableError):
        embed_texts(["some memory content"])


def test_no_zero_vectors_ever_leave_the_function(monkeypatch):
    """The property, stated directly: a returned vector is never all-zero."""
    monkeypatch.setattr(emb, "_embed_openai",
                        lambda t, m: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(emb, "_embed_fastembed",
                        lambda t: (_ for _ in ()).throw(RuntimeError("y")))
    monkeypatch.setattr(emb.settings, "embedding_provider", "openai", raising=False)
    try:
        vecs = embed_texts(["a"])
    except EmbeddingUnavailableError:
        return  # raising is the correct outcome
    assert all(any(v != 0.0 for v in vec) for vec in vecs), \
        "embed_texts returned a fabricated zero vector"


def test_fallback_still_used_when_it_works(monkeypatch):
    monkeypatch.setattr(emb, "_embed_openai",
                        lambda t, m: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(emb, "_embed_fastembed", lambda t: [[0.5] * 384 for _ in t])
    monkeypatch.setattr(emb.settings, "embedding_provider", "openai", raising=False)
    vecs = embed_texts(["content"])
    assert len(vecs) == 1 and vecs[0][0] == 0.5


def test_nothing_is_cached_on_failure(monkeypatch):
    """A failed embed must not poison the cache either."""
    monkeypatch.setattr(emb, "_embed_openai",
                        lambda t, m: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(emb, "_embed_fastembed",
                        lambda t: (_ for _ in ()).throw(RuntimeError("y")))
    monkeypatch.setattr(emb.settings, "embedding_provider", "openai", raising=False)
    with pytest.raises(EmbeddingUnavailableError):
        embed_texts(["memorable content"])
    assert not emb._embed_cache


def test_happy_path_unchanged(monkeypatch):
    monkeypatch.setattr(emb, "_embed_openai", lambda t, m: [[0.1] * 1536 for _ in t])
    monkeypatch.setattr(emb.settings, "embedding_provider", "openai", raising=False)
    vecs = embed_texts(["hello", "world"])
    assert len(vecs) == 2 and len(vecs[0]) == 1536
