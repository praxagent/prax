"""Embedding generation for the memory subsystem.

Supports three dense embedding providers:
  - OpenAI (text-embedding-3-small, 1536-dim) — default, fast, cheap
  - Ollama (e.g. nomic-embed-text) — local, no data leaves your machine
  - fastembed (BAAI/bge-small-en-v1.5, 384-dim) — in-process fallback

For sparse vectors, uses a TF-IDF term-frequency approach compatible
with Qdrant's named sparse vector format.

References:
  - Karpukhin et al., "Dense Passage Retrieval for Open-Domain QA" (2020).
  - OpenAI text-embedding-3-small: 1536-dim, $0.02/1M tokens.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter

from prax.settings import settings

logger = logging.getLogger(__name__)

# In-memory embedding cache (content hash → vector) to avoid re-embedding
# the same text.  Bounded by Python process lifetime.
_embed_cache: dict[str, list[float]] = {}
_MAX_CACHE = 5000


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate dense embeddings for a batch of texts.

    Uses OpenAI by default (text-embedding-3-small, 1536-dim).
    Falls back to fastembed for offline / local mode.
    """
    if not texts:
        return []

    # Check cache first
    hashes = [_content_hash(t) for t in texts]
    cached = {h: _embed_cache[h] for h in hashes if h in _embed_cache}
    if len(cached) == len(texts):
        return [cached[h] for h in hashes]

    # Determine which texts need embedding
    uncached_indices = [i for i, h in enumerate(hashes) if h not in cached]
    uncached_texts = [texts[i] for i in uncached_indices]

    model = getattr(settings, "embedding_model", "text-embedding-3-small")
    provider = getattr(settings, "embedding_provider", "openai")

    try:
        if provider == "openai":
            vectors = _embed_openai(uncached_texts, model)
        elif provider == "ollama":
            vectors = _embed_ollama(uncached_texts, model)
        else:
            vectors = _embed_fastembed(uncached_texts)
    except Exception:
        logger.exception("Primary embedding failed — trying fallback")
        try:
            vectors = _embed_fastembed(uncached_texts)
        except Exception:
            logger.exception("Fallback embedding also failed")
            # Return zero vectors as last resort (search quality will degrade)
            dim = 1536 if provider == "openai" else 384
            vectors = [[0.0] * dim for _ in uncached_texts]

    # Populate cache
    for idx, vec in zip(uncached_indices, vectors):
        h = hashes[idx]
        if len(_embed_cache) < _MAX_CACHE:
            _embed_cache[h] = vec

    # Assemble result in original order
    result: list[list[float]] = []
    vec_iter = iter(vectors)
    for h in hashes:
        if h in cached:
            result.append(cached[h])
        else:
            result.append(next(vec_iter))
    return result


def embed_text(text: str) -> list[float]:
    """Embed a single text."""
    return embed_texts([text])[0]


def _embed_openai(texts: list[str], model: str) -> list[list[float]]:
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_key)
    # OpenAI supports up to 2048 texts per batch
    all_vectors: list[list[float]] = []
    batch_size = 2048
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(input=batch, model=model)
        for item in resp.data:
            all_vectors.append(item.embedding)
    return all_vectors


def _embed_ollama(texts: list[str], model: str) -> list[list[float]]:
    import httpx

    base_url = getattr(settings, "ollama_base_url", "http://localhost:11434")
    url = f"{base_url.rstrip('/')}/api/embed"
    # Ollama's /api/embed accepts a list of texts in a single call
    resp = httpx.post(url, json={"model": model, "input": texts}, timeout=60.0)
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _embed_fastembed(texts: list[str]) -> list[list[float]]:
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    return [list(v) for v in model.embed(texts)]


# ---------------------------------------------------------------------------
# Sparse vectors — simple TF-IDF for hybrid search
# ---------------------------------------------------------------------------

# Minimal stop words to avoid matching noise
_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would shall should may might can could and or but if then else "
    "for of to in on at by with from as into about between through".split()
)

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def sparse_encode(text: str) -> dict[int, float]:
    """Produce a sparse vector from text using TF-IDF-style weighting.

    Returns a dict mapping token_hash → weight, compatible with Qdrant's
    SparseVector format (indices + values).
    """
    tokens = [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOP_WORDS]
    if not tokens:
        return {}

    tf = Counter(tokens)
    max_tf = max(tf.values())

    sparse: dict[int, float] = {}
    for token, count in tf.items():
        # Augmented TF to prevent bias toward long documents
        weight = 0.5 + 0.5 * (count / max_tf)
        # Use a stable hash for the token → index mapping
        idx = int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % (2**31)
        # Log-scale to dampen very frequent terms
        sparse[idx] = math.log1p(weight)
    return sparse
