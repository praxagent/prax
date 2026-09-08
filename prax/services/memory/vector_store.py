"""Qdrant-backed vector store for long-term semantic memory.

Stores memory chunks as dense (text-embedding-3-small, 1536-dim) and sparse
(TF-IDF) vectors with rich payload metadata.  All queries are scoped by
user_id for isolation.

Decay is time-based only: a memory's effective importance is its stored
importance times exp(-λ × days since it was last accessed), and memories
whose effective importance falls below the prune threshold are deleted.
The stored importance is never rewritten by the decay pass, so running it
twice for the same moment is a no-op (see `decay_memories`).  An
interaction-count decay signal existed on paper until 2026-09 but was never
wired to a caller; it was removed rather than armed.

Gracefully degrades: reads return empty results and log warnings when Qdrant
is unreachable.  Writes do NOT degrade silently — `upsert_memory` raises
`MemoryWriteError` so callers cannot report a memory as stored when it is not.

References:
  - Lewis et al., "Retrieval-Augmented Generation" (2020): RAG foundation.
  - Karpukhin et al., "Dense Passage Retrieval" (2020): dense retrieval evidence.
  - Zhong et al., "MemoryBank" (2023): forgetting curve, reset on recall.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from prax.services.memory.models import MemoryResult
from prax.settings import settings

logger = logging.getLogger(__name__)

COLLECTION = "prax_memories"


class MemoryWriteError(RuntimeError):
    """A memory write did not reach the vector store.

    Raised by `upsert_memory` instead of returning a memory_id for a write
    that failed.  Before 2026-09 the function swallowed every Qdrant error
    and returned the id anyway, so `MemoryService.remember`, the
    `memory_remember` tool and the TeamWork memory API all reported success
    for writes that never happened.
    """

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
    # check_compatibility=False skips a server-version round-trip (and its
    # noisy warning) on each client init — irrelevant for our usage.
    return QdrantClient(url=url, timeout=10, check_compatibility=False)


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

    Returns the memory_id (UUID) of the stored point.

    Raises:
        MemoryWriteError: the point was not written (Qdrant unreachable,
            collection creation failed, the server rejected the point, ...).
            Callers must treat this as "not remembered" — never as an id.
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
                "tags": tags or [],
                "entity_ids": entity_ids or [],
                "summary_level": summary_level,
            },
        )
        if sparse_vectors:
            point.vector.update(sparse_vectors)

        client.upsert(collection_name=COLLECTION, points=[point])
        return mid

    except Exception as exc:
        logger.exception("Failed to upsert memory %s to Qdrant", mid)
        raise MemoryWriteError(f"vector store write failed for memory {mid}: {exc}") from exc


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


def reinforce_memory(memory_id: str) -> None:
    """Bump access_count and last_accessed for a retrieved memory.

    Implements the "strengthen on recall" pattern from MemoryBank
    (Zhong et al., 2023).  Resetting `last_accessed` restarts the decay
    clock for this memory (see `decay_memories`).
    """
    try:
        client = _get_client()
        now = datetime.now(UTC).isoformat()

        # Qdrant doesn't support atomic increment, so we read-modify-write
        points = client.retrieve(collection_name=COLLECTION, ids=[memory_id], with_payload=True)
        if not points:
            return
        payload = points[0].payload or {}
        client.set_payload(
            collection_name=COLLECTION,
            payload={
                "access_count": payload.get("access_count", 0) + 1,
                "last_accessed": now,
            },
            points=[memory_id],
        )
    except Exception:
        logger.debug("Memory reinforcement failed for %s", memory_id, exc_info=True)


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


def effective_importance(
    stored_importance: float,
    last_accessed: datetime,
    now: datetime,
    halflife_days: float,
) -> float:
    """Ebbinghaus-decayed importance: stored × exp(-λ × days since last access).

    Pure function of (stored value, last access, now) — the stored value is
    never rewritten, so the result is the same however many times it is
    evaluated for the same `now`.
    """
    import math

    lambda_t = math.log(2) / halflife_days
    days_elapsed = (now - last_accessed).total_seconds() / 86400
    return stored_importance * math.exp(-lambda_t * days_elapsed)


def decay_memories(
    user_id: str,
    halflife_days: float = 7.0,
    prune_threshold: float = 0.02,
    now: datetime | None = None,
) -> int:
    """Prune memories whose time-decayed importance has fallen below threshold.

    # INVARIANT: this pass is idempotent for a given `now`.  It computes
    # effective_importance = stored_importance × exp(-λ × days_since_last_access)
    # and DELETES memories below `prune_threshold`; it never writes the decayed
    # value back.  Before 2026-09 it did write it back (`set_payload`) while
    # still measuring days from `last_accessed`, so every pass multiplied the
    # already-decayed value by the full factor again — and the pass ran every
    # 5 turns, so a "7-day half-life" pruned an active user's memories in 4-8
    # days.  With no write-back, a memory's fate depends only on how long it
    # has gone unrecalled, and a recall (`reinforce_memory` resets
    # `last_accessed`) restores it fully, which is the forgetting-curve
    # semantics the docs cite.

    `now` is injectable for tests; production callers leave it None.
    Returns the number of memories pruned.

    References:
      - Zhong et al., "MemoryBank" (2023): forgetting curve integration.
      - Park et al., "Generative Agents" (2023): exponential recency decay.
    """
    now = now or datetime.now(UTC)
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
            for r in records:
                payload = r.payload or {}
                last_accessed = payload.get("last_accessed", payload.get("created_at", ""))
                if not last_accessed:
                    continue
                try:
                    last_dt = datetime.fromisoformat(last_accessed.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    continue
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=UTC)

                current = effective_importance(
                    payload.get("importance", 0.5), last_dt, now, halflife_days
                )
                if current < prune_threshold:
                    ids_to_delete.append(r.id)

            if ids_to_delete:
                client.delete(collection_name=COLLECTION, points_selector=ids_to_delete)
                pruned += len(ids_to_delete)

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
