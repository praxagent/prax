"""Qdrant-backed vector store for long-term semantic memory.

Stores memory chunks as dense (text-embedding-3-small, 1536-dim) and sparse
(TF-IDF) vectors with rich payload metadata.  All queries are scoped by
user_id for isolation.

Decay uses both wall-clock time AND interaction count (how many conversations
have happened since the memory was last accessed), taking the maximum of both
decay signals.  This handles both "gone for a week" and "100 conversations but
never mentioned X" scenarios.

Gracefully degrades: if Qdrant is unreachable, operations return empty
results and log warnings (no crashes).

References:
  - Lewis et al., "Retrieval-Augmented Generation" (2020): RAG foundation.
  - Karpukhin et al., "Dense Passage Retrieval" (2020): dense retrieval evidence.
  - FOREVER (2026): interaction-based decay outperforms pure wall-clock decay.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from prax.services.memory.models import MemoryResult
from prax.settings import settings

logger = logging.getLogger(__name__)

COLLECTION = "prax_memories"

# Dense vector dimension per embedding provider.
_PROVIDER_DIM = {"openai": 1536, "ollama": 768, "local": 384}


def _dense_dim() -> int:
    """Return the expected dense vector dimension for the configured provider."""
    provider = getattr(settings, "embedding_provider", "openai")
    return _PROVIDER_DIM.get(provider, 1536)


def _get_client():
    """Lazy-init the Qdrant client."""
    from qdrant_client import QdrantClient

    url = getattr(settings, "qdrant_url", "http://localhost:6333")
    return QdrantClient(url=url, timeout=10)


def _ensure_collection(client) -> None:
    """Create the memories collection if it doesn't exist."""
    from qdrant_client.models import (
        Distance,
        SparseIndexParams,
        SparseVectorParams,
        VectorParams,
    )

    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION in collections:
        return

    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": VectorParams(size=_dense_dim(), distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(index=SparseIndexParams()),
        },
    )
    # Create payload indexes for fast filtering
    from qdrant_client.models import PayloadSchemaType

    client.create_payload_index(COLLECTION, "user_id", PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION, "source", PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION, "tags", PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION, "created_at", PayloadSchemaType.DATETIME)
    logger.info("Created Qdrant collection '%s'", COLLECTION)


def upsert_memory(
    user_id: str,
    content: str,
    dense_vector: list[float],
    sparse_vector: dict[int, float] | None = None,
    source: str = "conversation",
    importance: float = 0.5,
    tags: list[str] | None = None,
    entity_ids: list[str] | None = None,
    summary_level: str = "raw",
    memory_id: str | None = None,
) -> str:
    """Store a memory chunk with dense + sparse embeddings.

    Returns the memory_id (UUID).
    """
    mid = memory_id or str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    try:
        client = _get_client()
        _ensure_collection(client)

        from qdrant_client.models import PointStruct, SparseVector

        vectors: dict = {"dense": dense_vector}
        sparse_vectors = {}
        if sparse_vector:
            indices = sorted(sparse_vector.keys())
            values = [sparse_vector[i] for i in indices]
            sparse_vectors["sparse"] = SparseVector(indices=indices, values=values)

        point = PointStruct(
            id=mid,
            vector=vectors,
            payload={
                "user_id": user_id,
                "content": content,
                "source": source,
                "importance": importance,
                "created_at": now,
                "last_accessed": now,
                "access_count": 0,
                "interaction_epoch": 0,  # global interaction count when stored
                "tags": tags or [],
                "entity_ids": entity_ids or [],
                "summary_level": summary_level,
            },
        )
        if sparse_vectors:
            point.vector.update(sparse_vectors)

        client.upsert(collection_name=COLLECTION, points=[point])
        return mid

    except Exception:
        logger.exception("Failed to upsert memory to Qdrant")
        return mid


def search_dense(
    user_id: str,
    query_vector: list[float],
    top_k: int = 10,
    min_importance: float = 0.0,
) -> list[MemoryResult]:
    """Dense (semantic) vector search, scoped to user_id."""
    try:
        client = _get_client()
        _ensure_collection(client)

        from qdrant_client.models import FieldCondition, Filter, MatchValue, Range

        conditions = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if min_importance > 0:
            conditions.append(
                FieldCondition(key="importance", range=Range(gte=min_importance))
            )

        resp = client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            using="dense",
            query_filter=Filter(must=conditions),
            limit=top_k,
            with_payload=True,
        )
        return [_to_memory_result(r) for r in resp.points]

    except Exception:
        logger.exception("Qdrant dense search failed")
        return []


def search_sparse(
    user_id: str,
    sparse_vector: dict[int, float],
    top_k: int = 10,
) -> list[MemoryResult]:
    """Sparse (BM25-style) vector search, scoped to user_id."""
    try:
        client = _get_client()
        _ensure_collection(client)

        from qdrant_client.models import FieldCondition, Filter, MatchValue, SparseVector

        indices = sorted(sparse_vector.keys())
        values = [sparse_vector[i] for i in indices]

        resp = client.query_points(
            collection_name=COLLECTION,
            query=SparseVector(indices=indices, values=values),
            using="sparse",
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
            limit=top_k,
            with_payload=True,
        )
        return [_to_memory_result(r) for r in resp.points]

    except Exception:
        logger.exception("Qdrant sparse search failed")
        return []


def reinforce_memory(memory_id: str, interaction_epoch: int = 0) -> None:
    """Bump access_count, last_accessed, and interaction_epoch for a retrieved memory.

    Implements the "strengthen on recall" pattern from MemoryBank
    (Zhong et al., 2023), extended with interaction-based tracking
    (FOREVER, 2026).
    """
    try:
        client = _get_client()
        now = datetime.now(UTC).isoformat()

        # Qdrant doesn't support atomic increment, so we read-modify-write
        points = client.retrieve(collection_name=COLLECTION, ids=[memory_id], with_payload=True)
        if not points:
            return
        payload = points[0].payload or {}
        update: dict = {
            "access_count": payload.get("access_count", 0) + 1,
            "last_accessed": now,
        }
        if interaction_epoch > 0:
            update["interaction_epoch"] = interaction_epoch
        client.set_payload(
            collection_name=COLLECTION,
            payload=update,
            points=[memory_id],
        )
    except Exception:
        logger.debug("Memory reinforcement failed for %s", memory_id, exc_info=True)


def _epoch_point_id(user_id: str) -> str:
    """Deterministic UUID for a user's epoch counter sentinel point."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"prax:epoch:{user_id}"))


def get_interaction_epoch(user_id: str) -> int:
    """Return the current interaction epoch (message count) for a user.

    Stored as a sentinel point in Qdrant with a deterministic UUID.
    Falls back to 0 if no data is available.
    """
    try:
        client = _get_client()
        _ensure_collection(client)

        points = client.retrieve(
            collection_name=COLLECTION,
            ids=[_epoch_point_id(user_id)],
            with_payload=True,
        )
        if points:
            return points[0].payload.get("epoch", 0)
        return 0
    except Exception:
        return 0


def increment_interaction_epoch(user_id: str) -> int:
    """Increment and return the interaction epoch for a user.

    Called once per user message to advance the epoch counter.
    """
    try:
        client = _get_client()
        _ensure_collection(client)
        from qdrant_client.models import PointStruct

        current = get_interaction_epoch(user_id)
        new_epoch = current + 1
        # Store as a sentinel point with a zero vector
        point = PointStruct(
            id=_epoch_point_id(user_id),
            vector={"dense": [0.0] * _dense_dim()},
            payload={
                "user_id": f"_system_{user_id}",
                "content": "",
                "epoch": new_epoch,
                "source": "_epoch_counter",
            },
        )
        client.upsert(collection_name=COLLECTION, points=[point])
        return new_epoch
    except Exception:
        logger.debug("Failed to increment interaction epoch for %s", user_id)
        return 0


def delete_memory(memory_id: str) -> bool:
    """Delete a single memory by ID."""
    try:
        client = _get_client()
        client.delete(collection_name=COLLECTION, points_selector=[memory_id])
        return True
    except Exception:
        logger.exception("Failed to delete memory %s", memory_id)
        return False


def get_user_memory_count(user_id: str) -> int:
    """Return the number of memories stored for a user."""
    try:
        client = _get_client()
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        result = client.count(
            collection_name=COLLECTION,
            count_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
        )
        return result.count
    except Exception:
        return 0


def decay_memories(
    user_id: str,
    halflife_days: float = 7.0,
    halflife_interactions: int = 100,
    prune_threshold: float = 0.02,
) -> int:
    """Apply dual decay to all user memories: time-based AND interaction-based.

    Decay signals:
      time_decay   = exp(-λ_t × days_since_last_access)
      interaction_decay = exp(-λ_i × interactions_since_last_access)

    The effective decay is the minimum of both (strongest decay wins).
    This handles both "gone for a week" and "100 conversations but never
    mentioned X" scenarios.

    Memories below prune_threshold are deleted.
    Returns the number of memories pruned.

    References:
      - Zhong et al., "MemoryBank" (2023): forgetting curve integration.
      - Park et al., "Generative Agents" (2023): exponential recency decay.
      - FOREVER (2026): interaction-based decay outperforms wall-clock decay.
    """
    import math

    lambda_t = math.log(2) / halflife_days
    lambda_i = math.log(2) / halflife_interactions if halflife_interactions > 0 else 0.0
    now = datetime.now(UTC)
    current_epoch = get_interaction_epoch(user_id)
    pruned = 0

    try:
        client = _get_client()
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        # Scroll through all user memories
        offset = None
        while True:
            records, offset = client.scroll(
                collection_name=COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
                ),
                limit=100,
                offset=offset,
                with_payload=True,
            )
            if not records:
                break

            ids_to_delete = []
            ids_to_update = []

            for r in records:
                payload = r.payload or {}
                # Skip sentinel points (epoch counters)
                if payload.get("source") == "_epoch_counter":
                    continue

                last_accessed = payload.get("last_accessed", payload.get("created_at", ""))
                if not last_accessed:
                    continue

                try:
                    last_dt = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue

                current_imp = payload.get("importance", 0.5)

                # Time-based decay
                days_elapsed = (now - last_dt).total_seconds() / 86400
                time_factor = math.exp(-lambda_t * days_elapsed)

                # Interaction-based decay
                mem_epoch = payload.get("interaction_epoch", 0)
                interaction_gap = max(0, current_epoch - mem_epoch)
                interaction_factor = math.exp(-lambda_i * interaction_gap) if lambda_i > 0 else 1.0

                # Use the stronger decay signal (minimum factor)
                decay_factor = min(time_factor, interaction_factor)
                new_imp = current_imp * decay_factor

                if new_imp < prune_threshold:
                    ids_to_delete.append(r.id)
                elif abs(new_imp - current_imp) > 0.001:
                    ids_to_update.append((r.id, new_imp))

            if ids_to_delete:
                client.delete(collection_name=COLLECTION, points_selector=ids_to_delete)
                pruned += len(ids_to_delete)

            for mid, imp in ids_to_update:
                client.set_payload(
                    collection_name=COLLECTION,
                    payload={"importance": round(imp, 4)},
                    points=[mid],
                )

            if offset is None:
                break

    except Exception:
        logger.exception("Memory decay failed for user %s", user_id)

    return pruned


def _to_memory_result(scored_point) -> MemoryResult:
    payload = scored_point.payload or {}
    return MemoryResult(
        memory_id=str(scored_point.id),
        content=payload.get("content", ""),
        score=scored_point.score,
        source=payload.get("source", "unknown"),
        importance=payload.get("importance", 0.5),
        created_at=payload.get("created_at", ""),
        entities=payload.get("entity_ids", []),
        metadata={
            k: v
            for k, v in payload.items()
            if k not in ("content", "user_id", "source", "importance", "created_at", "entity_ids")
        },
    )
