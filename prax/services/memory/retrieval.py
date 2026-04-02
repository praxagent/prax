"""Hybrid retrieval engine — combines vector, sparse, and graph retrieval.

Uses Reciprocal Rank Fusion (RRF) to merge ranked lists from multiple
retrieval sources into a single ranking.  Post-processes with time decay
and importance boosting.

Pipeline:
  1. Dense vector search (Qdrant) → ranked list
  2. Sparse BM25-style search (Qdrant) → ranked list
  3. Graph neighbourhood search (Neo4j) → ranked list
  4. RRF fusion of all lists
  5. Time decay modifier
  6. Importance boost
  7. Access reinforcement for returned results

References:
  - Cormack et al., "Reciprocal Rank Fusion" (SIGIR 2009): robust rank fusion.
  - Park et al., "Generative Agents" (2023): relevance + recency + importance.
  - He et al., "HippoRAG" (2024): graph neighbourhood retrieval.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone

from prax.services.memory.models import MemoryResult

logger = logging.getLogger(__name__)

# RRF constant — standard value from Cormack et al. (2009)
RRF_K = 60


def hybrid_search(
    user_id: str,
    query: str,
    top_k: int = 10,
    time_decay: bool = True,
    min_importance: float = 0.0,
    halflife_days: float = 7.0,
) -> list[MemoryResult]:
    """Run hybrid retrieval across all memory stores and return fused results.

    Combines dense vector search, sparse keyword search, and graph
    neighbourhood expansion, then fuses via RRF with time/importance
    adjustments.
    """
    from prax.services.memory import embedder, graph_store, vector_store

    # 1. Generate query embeddings
    query_dense = embedder.embed_text(query)
    query_sparse = embedder.sparse_encode(query)

    # 2. Run retrieval from each source in sequence (could be parallelised)
    dense_results = vector_store.search_dense(
        user_id, query_dense, top_k=top_k * 2, min_importance=min_importance
    )
    sparse_results = vector_store.search_sparse(user_id, query_sparse, top_k=top_k * 2)
    graph_results = _graph_neighbourhood_search(user_id, query, top_k=top_k)

    # 3. RRF fusion
    ranked_lists = [dense_results, sparse_results, graph_results]
    fused = rrf_fuse(ranked_lists)

    # 4. Apply time decay modifier
    if time_decay:
        lambda_ = math.log(2) / halflife_days
        now = datetime.now(timezone.utc)
        for r in fused:
            if r.created_at:
                try:
                    created = datetime.fromisoformat(r.created_at.replace("Z", "+00:00"))
                    days_old = (now - created).total_seconds() / 86400
                    r.score *= math.exp(-lambda_ * days_old)
                except (ValueError, AttributeError):
                    pass

    # 5. Importance boost — scale by (0.5 + importance) so higher importance
    #    memories get up to 1.5x boost
    for r in fused:
        r.score *= 0.5 + r.importance

    # 6. Re-sort after adjustments and take top_k
    fused.sort(key=lambda r: r.score, reverse=True)
    final = fused[:top_k]

    # 7. Reinforce accessed memories (async-safe, best-effort)
    for r in final:
        try:
            vector_store.reinforce_memory(r.memory_id)
        except Exception:
            pass

    return final


def rrf_fuse(ranked_lists: list[list[MemoryResult]]) -> list[MemoryResult]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    For each candidate appearing in any list:
        rrf_score = sum(1 / (k + rank_i)) for each list where it appears

    where k = 60 (standard constant from Cormack et al., 2009).
    """
    scores: dict[str, float] = {}
    best_result: dict[str, MemoryResult] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list):
            mid = result.memory_id
            scores[mid] = scores.get(mid, 0) + 1.0 / (RRF_K + rank + 1)
            # Keep the result with the highest original score for metadata
            if mid not in best_result or result.score > best_result[mid].score:
                best_result[mid] = result

    # Build fused list
    fused: list[MemoryResult] = []
    for mid, rrf_score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        r = best_result[mid]
        fused.append(
            MemoryResult(
                memory_id=r.memory_id,
                content=r.content,
                score=rrf_score,
                source=r.source,
                importance=r.importance,
                created_at=r.created_at,
                entities=r.entities,
                metadata=r.metadata,
            )
        )

    return fused


def _graph_neighbourhood_search(
    user_id: str,
    query: str,
    top_k: int = 10,
) -> list[MemoryResult]:
    """Extract key terms from query, find matching entities, and retrieve
    associated memories from the vector store.

    This implements the "graph neighbourhood" retrieval arm: entities found
    in the graph are used to pull associated memory chunks from the vector
    store via entity_ids payload matching.
    """
    from prax.services.memory import graph_store, vector_store

    # Extract key noun phrases (simple heuristic: capitalised words + long words)
    key_terms = _extract_key_terms(query)
    if not key_terms:
        return []

    # Search for entities matching key terms
    entity_ids: list[str] = []
    for term in key_terms[:5]:  # Limit to avoid too many graph queries
        entities = graph_store.search_entities(user_id, term, limit=3)
        for ent in entities:
            entity_ids.append(ent.get("id", ""))
            # Also get neighbours for multi-hop
            neighbours = graph_store._simple_neighbours(user_id, term.lower(), limit=5)
            # Collect entity names for context
            for n in neighbours:
                name = n.get("name", "")
                if name:
                    entities_from_name = graph_store.search_entities(user_id, name, limit=1)
                    for e in entities_from_name:
                        entity_ids.append(e.get("id", ""))

    if not entity_ids:
        return []

    # Deduplicate
    entity_ids = list(set(eid for eid in entity_ids if eid))

    # Search vector store for memories associated with these entities
    # We do this by embedding the entity-enriched query
    enriched_query = query + " " + " ".join(key_terms)
    from prax.services.memory.embedder import embed_text

    query_vec = embed_text(enriched_query)
    results = vector_store.search_dense(user_id, query_vec, top_k=top_k)

    # Boost results that are linked to our graph entities
    for r in results:
        overlap = set(r.entities) & set(entity_ids)
        if overlap:
            r.score *= 1.0 + 0.2 * len(overlap)  # 20% boost per matching entity

    return results


def _extract_key_terms(text: str) -> list[str]:
    """Extract likely key terms from a query string.

    Uses simple heuristics: capitalised words (proper nouns), long words
    (likely content words), and quoted phrases.
    """
    terms: list[str] = []

    # Quoted phrases
    for match in re.finditer(r'"([^"]+)"', text):
        terms.append(match.group(1))

    # Individual words (skip common short words)
    stop = {"what", "when", "where", "how", "why", "who", "which", "the", "a", "an",
            "is", "are", "was", "were", "do", "does", "did", "have", "has", "had",
            "about", "with", "from", "that", "this", "and", "but", "for", "you",
            "your", "my", "me", "we", "our", "they", "them", "their", "can",
            "could", "would", "should", "will", "know", "remember", "tell"}
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]+\b", text)
    for w in words:
        if w.lower() not in stop and (len(w) > 4 or w[0].isupper()):
            terms.append(w)

    return list(dict.fromkeys(terms))  # deduplicate preserving order
