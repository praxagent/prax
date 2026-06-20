"""Vector index for knowledge-graph concepts.

Knowledge concepts live in Neo4j (``KnowledgeConcept`` nodes), but substring
``CONTAINS`` matching can't find a concept whose wording differs from the
query.  This module mirrors each concept into a Qdrant collection (dense +
sparse vectors) so :func:`prax.services.memory.knowledge_graph.search_knowledge`
can do real semantic retrieval and fuse it with the keyword arm.

Everything degrades gracefully: when Qdrant / the embedder is unavailable
(lite deployments, tests), every function no-ops or returns empty and the
caller falls back to keyword matching.  The Qdrant client + embedder are
reused from the conversational-memory stack.
"""
from __future__ import annotations

import logging

from prax.settings import settings

logger = logging.getLogger(__name__)

COLLECTION = "prax_knowledge_concepts"


def available() -> bool:
    """Cheap gate — semantic concept retrieval requires memory infra."""
    return bool(getattr(settings, "memory_enabled", True)) and bool(
        getattr(settings, "knowledge_hybrid_enabled", True)
    )


def _client():
    from prax.services.memory.vector_store import _get_client
    return _get_client()


def _concept_text(display_name: str, description: str) -> str:
    parts = [p for p in (display_name or "", description or "") if p]
    return ". ".join(parts) or display_name or ""


def _ensure(client) -> None:
    from qdrant_client.models import (
        Distance,
        PayloadSchemaType,
        SparseIndexParams,
        SparseVectorParams,
        VectorParams,
    )

    from prax.services.memory.vector_store import _dense_dim

    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION in collections:
        return
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={"dense": VectorParams(size=_dense_dim(), distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams())},
    )
    client.create_payload_index(COLLECTION, "user_id", PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION, "namespace", PayloadSchemaType.KEYWORD)
    logger.info("Created Qdrant collection '%s'", COLLECTION)


def upsert_concept(
    user_id: str, namespace: str, concept_id: str, display_name: str, description: str = "",
) -> None:
    """Embed and store/refresh a concept's vectors (best-effort)."""
    if not available():
        return
    text = _concept_text(display_name, description)
    if not text:
        return
    try:
        from qdrant_client.models import PointStruct, SparseVector

        from prax.services.memory import embedder
        client = _client()
        _ensure(client)
        vectors: dict = {"dense": embedder.embed_text(text)}
        sparse = embedder.sparse_encode(text)
        if sparse:
            idx = sorted(sparse.keys())
            vectors["sparse"] = SparseVector(indices=idx, values=[sparse[i] for i in idx])
        client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=concept_id,
                vector=vectors,
                payload={"user_id": user_id, "namespace": namespace, "concept_id": concept_id},
            )],
        )
    except Exception:
        logger.debug("Concept vector upsert failed (degrading to keyword search)", exc_info=True)


def delete_concept(concept_id: str) -> None:
    if not available():
        return
    try:
        from qdrant_client.models import PointIdsList
        client = _client()
        client.delete(collection_name=COLLECTION, points_selector=PointIdsList(points=[concept_id]))
    except Exception:
        logger.debug("Concept vector delete failed", exc_info=True)


def delete_namespace_vectors(user_id: str, namespace: str) -> None:
    if not available():
        return
    try:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue
        client = _client()
        client.delete(
            collection_name=COLLECTION,
            points_selector=FilterSelector(filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="namespace", match=MatchValue(value=namespace)),
            ])),
        )
    except Exception:
        logger.debug("Concept vector namespace delete failed", exc_info=True)


def search(
    user_id: str, query: str, namespace: str | None = None, top_k: int = 20,
) -> list[tuple[str, float]]:
    """Hybrid (dense + sparse) concept search → ``[(concept_id, rrf_score)]``.

    Returns an empty list (caller falls back to keyword search) whenever the
    vector backend is unavailable or has no matches.
    """
    if not available() or not query.strip():
        return []
    try:
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            MatchValue,
            SparseVector,
        )

        from prax.services.memory import embedder
        client = _client()
        _ensure(client)

        must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if namespace:
            must.append(FieldCondition(key="namespace", match=MatchValue(value=namespace)))
        qfilter = Filter(must=must)

        dense = client.query_points(
            collection_name=COLLECTION, query=embedder.embed_text(query),
            using="dense", query_filter=qfilter, limit=top_k, with_payload=True,
        ).points

        sparse_map = embedder.sparse_encode(query)
        sparse_points = []
        if sparse_map:
            idx = sorted(sparse_map.keys())
            sparse_points = client.query_points(
                collection_name=COLLECTION,
                query=SparseVector(indices=idx, values=[sparse_map[i] for i in idx]),
                using="sparse", query_filter=qfilter, limit=top_k, with_payload=True,
            ).points

        return _rrf([[p.id for p in dense], [p.id for p in sparse_points]])
    except Exception:
        logger.debug("Concept vector search failed (degrading to keyword search)", exc_info=True)
        return []


def _rrf(id_lists: list[list], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal-rank-fuse ranked id lists → sorted ``[(id, score)]``."""
    scores: dict[str, float] = {}
    for ranked in id_lists:
        for rank, cid in enumerate(ranked):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
