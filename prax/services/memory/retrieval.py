"""Hybrid retrieval engine — combines vector, sparse, and graph retrieval.

Uses *weighted* Reciprocal Rank Fusion (RRF) to merge ranked lists from
multiple retrieval sources.  Weights are adaptive: factual/entity queries
boost sparse + graph; semantic/open-ended queries boost dense.

Pipeline:
  1. Classify query intent (factual vs semantic)
  2. Dense vector search (Qdrant) → ranked list
  3. Sparse BM25-style search (Qdrant) → ranked list
  4. Graph neighbourhood search (Neo4j) → ranked list
  5. Weighted RRF fusion (weights from step 1)
  6. Time decay modifier
  7. Importance boost
  8. Access reinforcement for returned results

References:
  - Cormack et al., "Reciprocal Rank Fusion" (SIGIR 2009): robust rank fusion.
  - Park et al., "Generative Agents" (2023): relevance + recency + importance.
  - He et al., "HippoRAG" (2024): graph neighbourhood retrieval.
  - KG-retrieval (2025, arXiv:2511.18194): type-specific weighted RRF.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import UTC, datetime

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
    neighbourhood expansion, then fuses via *weighted* RRF with
    time/importance adjustments.  Weights are adaptive: factual queries
    boost sparse + graph; semantic queries boost dense.
    """
    from prax.services.memory import embedder, vector_store

    # 0. Classify query to determine retrieval weights
    weights = _classify_query_weights(query)

    # 1. Generate query embeddings
    query_sparse = embedder.sparse_encode(query)

    # 2. Run retrieval from each source in sequence (could be parallelised).
    #    Dense arm optionally unions across paraphrase/HyDE query variants to
    #    improve recall when the query's phrasing differs from the memory's.
    dense_results = _dense_arm(
        user_id, query, top_k=top_k * 2, min_importance=min_importance
    )
    sparse_results = vector_store.search_sparse(user_id, query_sparse, top_k=top_k * 2)
    graph_results = _graph_neighbourhood_search(user_id, query, top_k=top_k)

    # 3. Weighted RRF fusion
    ranked_lists = [dense_results, sparse_results, graph_results]
    fused = rrf_fuse(ranked_lists, weights=weights)

    # 4. Apply time decay modifier
    if time_decay:
        lambda_ = math.log(2) / halflife_days
        now = datetime.now(UTC)
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

    # 6. Re-sort after adjustments
    fused.sort(key=lambda r: r.score, reverse=True)

    # 6b. Optional relevance rerank — a precision stage that re-scores the top
    #     fused candidates against the query so low-relevance-but-recent/important
    #     memories can't outrank on-topic ones.
    from prax.settings import settings as _settings
    if _settings.retrieval_rerank_enabled and len(fused) > 1:
        fused = _rerank(query, fused, _settings.retrieval_rerank_candidates)

    # 7. Take top_k
    final = fused[:top_k]

    # 8. Reinforce accessed memories (async-safe, best-effort)
    for r in final:
        try:
            vector_store.reinforce_memory(r.memory_id)
        except Exception:
            pass

    return final


def rrf_fuse(
    ranked_lists: list[list[MemoryResult]],
    weights: list[float] | None = None,
) -> list[MemoryResult]:
    """Weighted Reciprocal Rank Fusion across multiple ranked result lists.

    For each candidate appearing in any list:
        rrf_score = sum(weight_i / (k + rank_i)) for each list where it appears

    where k = 60 (standard constant from Cormack et al., 2009).
    When weights=None, all lists are weighted equally (weight=1.0).

    Inspired by type-specific weighted RRF (arXiv:2511.18194).
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)

    scores: dict[str, float] = {}
    best_result: dict[str, MemoryResult] = {}

    for list_idx, ranked_list in enumerate(ranked_lists):
        w = weights[list_idx] if list_idx < len(weights) else 1.0
        for rank, result in enumerate(ranked_list):
            mid = result.memory_id
            scores[mid] = scores.get(mid, 0) + w / (RRF_K + rank + 1)
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


def _classify_query_weights(query: str) -> list[float]:
    """Classify query intent and return [dense_weight, sparse_weight, graph_weight].

    Factual/entity queries (names, codes, specific lookups) boost sparse + graph.
    Semantic/open-ended queries (feelings, preferences, general topics) boost dense.

    Heuristics (fast, no LLM call):
      - Quoted phrases / ALL-CAPS / identifiers → factual → boost sparse
      - Named entities (capitalised words) → entity → boost graph
      - Open-ended question words without specific terms → semantic → boost dense

    Default weights: [1.0, 1.0, 1.0] (equal, standard RRF).
    """
    # Defaults — equal weighting
    dense_w, sparse_w, graph_w = 1.0, 1.0, 1.0

    has_quotes = '"' in query
    has_identifier = bool(re.search(r"[A-Z]{2,}[-_]?\d+|[A-Z0-9]{4,}", query))
    capitalised_words = re.findall(r"\b[A-Z][a-z]{2,}\b", query)
    has_entities = len(capitalised_words) >= 2

    # Factual signals → boost sparse + graph
    if has_quotes or has_identifier:
        sparse_w = 1.5
        graph_w = 1.3
        dense_w = 0.8

    # Entity signals → boost graph
    if has_entities:
        graph_w = max(graph_w, 1.4)
        sparse_w = max(sparse_w, 1.2)

    # Semantic/open-ended signals → boost dense
    open_ended_patterns = [
        r"\b(how|why|what do you|tell me about|describe|explain)\b",
        r"\b(feel|prefer|think|opinion|style|approach)\b",
    ]
    if any(re.search(p, query, re.IGNORECASE) for p in open_ended_patterns):
        if not has_quotes and not has_identifier:
            dense_w = max(dense_w, 1.4)
            sparse_w = min(sparse_w, 0.9)

    return [dense_w, sparse_w, graph_w]


def _dense_arm(
    user_id: str,
    query: str,
    top_k: int,
    min_importance: float,
) -> list[MemoryResult]:
    """Dense vector retrieval, optionally unioned across query variants.

    With ``retrieval_query_expansion_enabled``, the query is expanded into a
    few paraphrase/HyDE variants; each is embedded and searched, and the
    candidates are unioned (deduped, keeping the best score per memory) before
    they enter RRF.  With expansion off, this is exactly the original single
    ``search_dense`` call — byte-for-byte unchanged behaviour.
    """
    from prax.services.memory import embedder, vector_store
    from prax.settings import settings

    if not settings.retrieval_query_expansion_enabled:
        return vector_store.search_dense(
            user_id, embedder.embed_text(query), top_k=top_k, min_importance=min_importance
        )

    variants = _expand_queries(query, settings.retrieval_query_expansion_n)
    best: dict[str, MemoryResult] = {}
    for v in variants:
        try:
            hits = vector_store.search_dense(
                user_id, embedder.embed_text(v), top_k=top_k, min_importance=min_importance
            )
        except Exception:
            continue
        for r in hits:
            existing = best.get(r.memory_id)
            if existing is None or r.score > existing.score:
                best[r.memory_id] = r
    merged = sorted(best.values(), key=lambda r: r.score, reverse=True)
    return merged[:top_k]


def _expand_queries(query: str, n: int) -> list[str]:
    """Return [query] plus up to n-1 paraphrase/HyDE variants (cheap LOW tier).

    Always includes the original query first.  Degrades to ``[query]`` on any
    failure so retrieval never breaks because expansion was unavailable.
    """
    if n <= 1:
        return [query]
    prompt = (
        "Rewrite the following search query in "
        f"{n - 1} different ways to improve recall over a personal-memory store. "
        "Vary the phrasing and include one hypothetical answer sentence (HyDE). "
        "Return ONLY the rewrites, one per line, no numbering.\n\n"
        f"Query: {query}"
    )
    raw = _llm_complete(prompt)
    if not raw:
        return [query]
    variants = [query]
    for line in raw.splitlines():
        cleaned = line.strip().lstrip("-*0123456789. ").strip()
        if cleaned and cleaned.lower() != query.lower() and cleaned not in variants:
            variants.append(cleaned)
        if len(variants) >= n:
            break
    return variants


def _rerank(query: str, candidates: list[MemoryResult], max_candidates: int) -> list[MemoryResult]:
    """Relevance-rerank the top fused candidates against the query (LLM judge).

    Sends up to ``max_candidates`` candidate snippets to a cheap LLM and asks
    for a 0-100 relevance score each; reorders the scored head by relevance and
    keeps the tail untouched.  Degrades to the input order on any failure.
    """
    head = candidates[:max_candidates]
    tail = candidates[max_candidates:]
    if len(head) < 2:
        return candidates

    listing = "\n".join(
        f"[{i}] {(r.content or '')[:240]}" for i, r in enumerate(head)
    )
    prompt = (
        "Score how relevant each numbered memory is to the query on a 0-100 "
        "scale (100 = directly answers it). Return one 'index:score' pair per "
        "line, nothing else.\n\n"
        f"Query: {query}\n\nMemories:\n{listing}"
    )
    raw = _llm_complete(prompt)
    if not raw:
        return candidates

    scores: dict[int, float] = {}
    for line in raw.splitlines():
        m = re.match(r"\s*\[?(\d+)\]?\s*[:=]\s*(\d+(?:\.\d+)?)", line)
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(head):
                scores[idx] = float(m.group(2))
    if not scores:
        return candidates

    # Reorder the head by rerank relevance (descending); unscored keep a low
    # default so they sink below scored-relevant ones but above nothing.
    reranked = sorted(head, key=lambda r: scores.get(head.index(r), -1.0), reverse=True)
    return reranked + tail


def _llm_complete(prompt: str) -> str:
    """Best-effort single-shot completion from the cheap retrieval LLM.

    Returns "" on any failure (no key, provider down, import error) so callers
    degrade gracefully.
    """
    try:
        from langchain_core.messages import HumanMessage

        from prax.agent.llm_factory import build_llm
        llm = build_llm(config_key="retrieval_assist", default_tier="low")
        resp = llm.invoke([HumanMessage(content=prompt)])
        return getattr(resp, "content", "") or ""
    except Exception:
        logger.debug("retrieval LLM assist unavailable", exc_info=True)
        return ""


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
