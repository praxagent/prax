"""Semantic search over past execution traces.

Prax's answer to "have I solved this problem before?" — embeds the
trigger (user intent) + top span summaries of every completed trace
and lets the agent look up similar prior work by natural language.

Design:
- Shares the existing Qdrant client + embedder stack used by
  ``prax.services.memory.vector_store`` but uses a separate
  collection (``prax_trace_summaries``) so memory and trace search
  don't pollute each other's ranking.
- **Lazy indexing.** The first time ``search_traces`` runs in a
  process, it scans ``.prax/graphs/graphs-*.jsonl`` and upserts any
  trace ID not already in the collection.  Subsequent calls skip
  already-indexed IDs via an in-memory set.
- **Graceful degradation.** When Qdrant is unreachable or the
  embedder fails, both ``search_traces`` and the index loop return a
  ``{"status": "not_available", ...}`` result — the agent never
  crashes from a missing observability layer.
- **Trace detail fetch** reads directly from the daily JSONL files
  (or in-memory active graphs) — no indexing required.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from prax.settings import settings

logger = logging.getLogger(__name__)

COLLECTION = "prax_trace_summaries"
MAX_DOC_CHARS = 1500
INDEX_BATCH_SIZE = 50

_indexed_cache: set[str] = set()
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Infrastructure availability
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """True when both Qdrant and an embedding provider are reachable."""
    try:
        from prax.services.memory import vector_store
        client = vector_store._get_client()
        client.get_collections()
        return True
    except Exception:
        return False


def _not_available(reason: str) -> dict:
    return {
        "status": "not_available",
        "message": (
            f"Trace semantic search is not available in this deployment "
            f"({reason}). Qdrant + an embedding provider must both be "
            "configured. In full-compose mode this works automatically; "
            "in lite mode it does not. Fall back to `review_my_traces` or "
            "`conversation_search` for keyword lookup."
        ),
    }


# ---------------------------------------------------------------------------
# Trace loading (from disk / in-memory graphs)
# ---------------------------------------------------------------------------

def _graphs_dir() -> Path:
    base = Path(settings.workspace_dir).resolve()
    return base / ".prax" / "graphs"


def _iter_persisted_traces():
    """Yield (trace_id, graph_dict) tuples for every persisted trace."""
    d = _graphs_dir()
    if not d.is_dir():
        return
    for path in sorted(d.glob("graphs-*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = data.get("trace_id")
                if tid:
                    yield tid, data
        except Exception as e:
            logger.debug("trace_search: failed reading %s: %s", path, e)


def _extract_search_document(graph: dict) -> str:
    """Build the text we embed for a single trace.

    Focus on signal: user intent (trigger) + a handful of span summaries.
    Truncated to MAX_DOC_CHARS so embedding cost and noise stay bounded.
    """
    parts: list[str] = []
    trigger = (graph.get("trigger") or "").strip()
    if trigger:
        parts.append(f"Task: {trigger}")
    nodes = graph.get("nodes") or []
    # Skip the root orchestrator node; we want the actual work spans.
    for node in nodes[:6]:
        summary = (node.get("summary") or "").strip()
        name = (node.get("name") or "").strip()
        if summary:
            parts.append(f"[{name}] {summary}")
    doc = "\n".join(parts).strip() or f"trace {graph.get('trace_id', '?')}"
    if len(doc) > MAX_DOC_CHARS:
        doc = doc[:MAX_DOC_CHARS] + "..."
    return doc


def _extract_summary_payload(graph: dict) -> dict:
    """Compact payload shown in search results."""
    nodes = graph.get("nodes") or []
    total_tool_calls = sum(n.get("tool_calls", 0) for n in nodes)
    started = nodes[0].get("started_at") if nodes else None
    return {
        "trace_id": graph.get("trace_id", ""),
        "trigger": (graph.get("trigger") or "")[:300],
        "status": graph.get("status", ""),
        "node_count": graph.get("node_count", len(nodes)),
        "tool_calls": total_tool_calls,
        "started_at": started,
        "session_id": graph.get("session_id", ""),
    }


# ---------------------------------------------------------------------------
# Collection lifecycle
# ---------------------------------------------------------------------------

def _ensure_collection(client) -> None:
    from qdrant_client.models import Distance, VectorParams

    from prax.services.memory.vector_store import _dense_dim

    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION in collections:
        return
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": VectorParams(size=_dense_dim(), distance=Distance.COSINE),
        },
    )
    try:
        from qdrant_client.models import PayloadSchemaType
        client.create_payload_index(
            COLLECTION, "trace_id", PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass
    logger.info("Created Qdrant collection '%s'", COLLECTION)


def _existing_trace_ids(client) -> set[str]:
    """Return the set of trace IDs already indexed in the collection."""
    try:
        ids: set[str] = set()
        next_page = None
        while True:
            resp, next_page = client.scroll(
                collection_name=COLLECTION,
                limit=256,
                offset=next_page,
                with_payload=["trace_id"],
                with_vectors=False,
            )
            for point in resp:
                tid = (point.payload or {}).get("trace_id")
                if tid:
                    ids.add(tid)
            if next_page is None:
                break
        return ids
    except Exception as e:
        logger.debug("trace_search: scroll failed: %s", e)
        return set()


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _index_new_traces() -> dict:
    """Scan persisted traces and upsert any that aren't indexed yet.

    Returns a status dict so callers can surface counts and errors.
    """
    try:
        from qdrant_client.models import PointStruct

        from prax.services.memory import embedder, vector_store
    except Exception as e:
        return _not_available(f"dependency import failed: {e}")

    try:
        client = vector_store._get_client()
        _ensure_collection(client)
    except Exception as e:
        return _not_available(f"Qdrant unreachable: {e}")

    with _cache_lock:
        cached = set(_indexed_cache)

    # Only probe the server once per process; otherwise trust the cache.
    if not cached:
        cached = _existing_trace_ids(client)
        with _cache_lock:
            _indexed_cache.update(cached)

    to_index: list[tuple[str, dict]] = []
    for trace_id, graph in _iter_persisted_traces():
        if trace_id in cached:
            continue
        to_index.append((trace_id, graph))

    if not to_index:
        return {"status": "ok", "indexed": 0, "skipped": len(cached)}

    indexed = 0
    for batch_start in range(0, len(to_index), INDEX_BATCH_SIZE):
        batch = to_index[batch_start:batch_start + INDEX_BATCH_SIZE]
        docs = [_extract_search_document(g) for _tid, g in batch]
        try:
            vectors = embedder.embed_texts(docs)
        except Exception as e:
            logger.warning("trace_search: embedding failed: %s", e)
            return {"status": "error", "error": f"embedding failed: {e}", "indexed": indexed}
        points = []
        for (tid, graph), vec in zip(batch, vectors, strict=False):
            payload = _extract_summary_payload(graph)
            # Qdrant point IDs must be UUID or int — derive a stable UUID
            # from the trace_id so re-runs are idempotent.
            import uuid as _uuid
            point_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, tid))
            points.append(PointStruct(id=point_id, vector={"dense": vec}, payload=payload))
        try:
            client.upsert(collection_name=COLLECTION, points=points)
            for tid, _g in batch:
                with _cache_lock:
                    _indexed_cache.add(tid)
            indexed += len(batch)
        except Exception as e:
            logger.warning("trace_search: upsert failed: %s", e)
            return {"status": "error", "error": f"upsert failed: {e}", "indexed": indexed}

    return {"status": "ok", "indexed": indexed, "skipped": len(cached)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_traces(query: str, top_k: int = 5) -> dict[str, Any]:
    """Return the top-k traces most similar to the query text.

    Result shape::

        {
          "status": "ok",
          "matches": [
            {"trace_id": ..., "trigger": ..., "status": ..., "score": 0.81, ...},
          ],
        }

    On missing infrastructure returns ``{"status": "not_available", "message": ...}``.
    """
    if not query.strip():
        return {"status": "error", "error": "query is empty"}
    top_k = max(1, min(top_k, 20))

    index_result = _index_new_traces()
    if index_result.get("status") == "not_available":
        return index_result

    try:
        from prax.services.memory import embedder, vector_store
    except Exception as e:
        return _not_available(f"dependency import failed: {e}")

    try:
        client = vector_store._get_client()
        _ensure_collection(client)
    except Exception as e:
        return _not_available(f"Qdrant unreachable: {e}")

    try:
        qvec = embedder.embed_text(query)
    except Exception as e:
        return {"status": "error", "error": f"embedding failed: {e}"}

    try:
        resp = client.query_points(
            collection_name=COLLECTION,
            query=qvec,
            using="dense",
            limit=top_k,
            with_payload=True,
        )
    except Exception as e:
        return {"status": "error", "error": f"query failed: {e}"}

    matches = []
    for point in resp.points:
        payload = dict(point.payload or {})
        payload["score"] = float(point.score) if point.score is not None else None
        matches.append(payload)
    return {"status": "ok", "matches": matches}


def get_trace_detail(trace_id: str) -> dict[str, Any]:
    """Return the full structured record of a specific trace.

    Looks up in-memory active graphs first (recent and still-running
    work) then falls back to the daily JSONL files. Returns
    ``{"status": "not_found", ...}`` if nothing matches.
    """
    if not trace_id or not trace_id.strip():
        return {"status": "error", "error": "trace_id is empty"}
    trace_id = trace_id.strip()

    # Try in-memory first.
    try:
        from prax.agent.trace import _active_graphs, _load_persisted_graphs
        _load_persisted_graphs()
        graph = _active_graphs.get(trace_id)
        if graph is not None:
            return {"status": "ok", "trace": graph.to_dict()}
    except Exception as e:
        logger.debug("trace_search: in-memory lookup failed: %s", e)

    # Fall back to scanning the JSONL files.
    for tid, data in _iter_persisted_traces():
        if tid == trace_id:
            return {"status": "ok", "trace": data}
    return {"status": "not_found", "message": f"No trace with id {trace_id!r}"}
