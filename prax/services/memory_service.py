"""Unified memory service — single entry point for all memory operations.

Orchestrates short-term memory (workspace-backed scratchpad), long-term
vector memory (Qdrant), graph memory (Neo4j), and hybrid retrieval (RRF
fusion).  All operations gracefully degrade when infrastructure is
unavailable.

Architecture (two-phase, research-aligned):
  Short-term:  bounded scratchpad → compaction when full
  Long-term:   vector store (semantic) + graph (relational) + consolidation

References:
  - Lewis et al., "RAG" (2020): vector retrieval foundation.
  - Park et al., "Generative Agents" (2023): relevance + recency + importance.
  - Packer et al., "MemGPT" (2023): virtual memory paging.
  - Zhong et al., "MemoryBank" (2023): hierarchical summaries + forgetting.
  - He et al., "HippoRAG" (2024): graph + PPR retrieval.
  - Cormack et al., "RRF" (2009): reciprocal rank fusion.
  - Edge et al., "GraphRAG" (2024): entity graphs + community summaries.
"""
from __future__ import annotations

import logging

from prax.services.memory.models import ConsolidationResult, Entity, MemoryResult, STMEntry
from prax.settings import settings

logger = logging.getLogger(__name__)

_instance = None


class MemoryService:
    """Unified interface for Prax's memory system."""

    def __init__(self) -> None:
        self._available = getattr(settings, "memory_enabled", False)
        if not self._available:
            logger.info("Memory system disabled (MEMORY_ENABLED=false)")

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Short-term memory (scratchpad)
    # ------------------------------------------------------------------

    def stm_write(
        self,
        user_id: str,
        key: str,
        content: str,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> STMEntry:
        """Write or update a scratchpad entry."""
        from prax.services.memory.stm import stm_write
        return stm_write(user_id, key, content, tags, importance)

    def stm_read(self, user_id: str, key: str | None = None) -> list[STMEntry]:
        """Read scratchpad entries."""
        from prax.services.memory.stm import stm_read
        return stm_read(user_id, key)

    def stm_delete(self, user_id: str, key: str) -> bool:
        """Delete a scratchpad entry."""
        from prax.services.memory.stm import stm_delete
        return stm_delete(user_id, key)

    def stm_compact(self, user_id: str) -> str:
        """Compact old STM entries via LLM summarization."""
        from prax.services.memory.stm import stm_compact
        return stm_compact(user_id)

    # ------------------------------------------------------------------
    # Long-term memory — store and forget
    # ------------------------------------------------------------------

    def remember(
        self,
        user_id: str,
        content: str,
        source: str = "conversation",
        importance: float = 0.5,
        tags: list[str] | None = None,
        entity_ids: list[str] | None = None,
    ) -> str:
        """Store a memory in the vector store.  Returns memory_id."""
        if not self._available:
            return ""
        try:
            from prax.services.memory.embedder import embed_text, sparse_encode
            from prax.services.memory.vector_store import upsert_memory

            dense = embed_text(content)
            sparse = sparse_encode(content)
            return upsert_memory(
                user_id=user_id,
                content=content,
                dense_vector=dense,
                sparse_vector=sparse,
                source=source,
                importance=importance,
                tags=tags,
                entity_ids=entity_ids,
            )
        except Exception:
            logger.exception("remember() failed")
            return ""

    def forget(self, user_id: str, memory_id: str) -> bool:
        """Delete a specific memory."""
        if not self._available:
            return False
        try:
            from prax.services.memory.vector_store import delete_memory
            return delete_memory(memory_id)
        except Exception:
            logger.exception("forget() failed")
            return False

    # ------------------------------------------------------------------
    # Recall — hybrid retrieval
    # ------------------------------------------------------------------

    def recall(
        self,
        user_id: str,
        query: str,
        top_k: int = 5,
        time_decay: bool = True,
        min_importance: float = 0.0,
    ) -> list[MemoryResult]:
        """Semantic recall from long-term memory using hybrid retrieval."""
        if not self._available:
            return []
        try:
            from prax.services.memory.retrieval import hybrid_search
            return hybrid_search(
                user_id=user_id,
                query=query,
                top_k=top_k,
                time_decay=time_decay,
                min_importance=min_importance,
            )
        except Exception:
            logger.exception("recall() failed")
            return []

    # ------------------------------------------------------------------
    # Graph operations
    # ------------------------------------------------------------------

    def add_entity(
        self,
        user_id: str,
        name: str,
        entity_type: str,
        importance: float = 0.5,
        properties: dict | None = None,
    ) -> str:
        """Upsert an entity in the knowledge graph."""
        if not self._available:
            return ""
        try:
            from prax.services.memory.graph_store import merge_entity
            return merge_entity(user_id, name, entity_type, importance=importance, properties=properties)
        except Exception:
            logger.exception("add_entity() failed")
            return ""

    def add_relation(
        self,
        user_id: str,
        source: str,
        relation_type: str,
        target: str,
        weight: float = 1.0,
    ) -> bool:
        """Add a typed relation between two entities."""
        if not self._available:
            return False
        try:
            from prax.services.memory.graph_store import add_relation
            return add_relation(user_id, source, relation_type, target, weight)
        except Exception:
            logger.exception("add_relation() failed")
            return False

    def entity_lookup(self, user_id: str, name: str) -> Entity | None:
        """Look up an entity and its relations."""
        if not self._available:
            return None
        try:
            from prax.services.memory.graph_store import get_entity
            return get_entity(user_id, name)
        except Exception:
            logger.exception("entity_lookup() failed")
            return None

    def graph_query(self, user_id: str, question: str) -> str:
        """Answer a graph-structure question about entities and relations."""
        if not self._available:
            return "Memory system not available."
        try:
            from prax.services.memory.graph_store import search_entities, get_entity

            # Extract key terms and search for matching entities
            from prax.services.memory.retrieval import _extract_key_terms
            terms = _extract_key_terms(question)

            results: list[str] = []
            seen: set[str] = set()
            for term in terms[:5]:
                entities = search_entities(user_id, term, limit=3)
                for ent_summary in entities:
                    name = ent_summary.get("name", "")
                    if name in seen:
                        continue
                    seen.add(name)
                    full = get_entity(user_id, name)
                    if full:
                        parts = [f"**{full.display_name}** ({full.entity_type}, importance={full.importance:.2f}, mentioned {full.mention_count}x)"]
                        for rel in full.relations[:10]:
                            direction = rel.get("direction", "")
                            arrow = "→" if direction == "outgoing" else "←"
                            parts.append(
                                f"  {arrow} {rel.get('type', '?')} {rel.get('other_name', '?')} "
                                f"(weight={rel.get('weight', 0):.1f})"
                            )
                        results.append("\n".join(parts))

            if not results:
                return f"No entities found matching: {', '.join(terms)}"
            return "\n\n".join(results)
        except Exception:
            logger.exception("graph_query() failed")
            return "Graph query failed."

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def consolidate(self, user_id: str) -> ConsolidationResult:
        """Run the full consolidation pipeline for a user."""
        if not self._available:
            return ConsolidationResult()
        try:
            from prax.services.memory.consolidation import consolidate_user
            return consolidate_user(user_id)
        except Exception:
            logger.exception("consolidate() failed")
            return ConsolidationResult()

    # ------------------------------------------------------------------
    # Stats / diagnostics
    # ------------------------------------------------------------------

    def stats(self, user_id: str) -> dict:
        """Return memory system statistics for a user."""
        result: dict = {"memory_enabled": self._available}
        if not self._available:
            return result

        try:
            from prax.services.memory.stm import stm_read
            stm_entries = stm_read(user_id)
            result["stm_entries"] = len(stm_entries)
        except Exception:
            result["stm_entries"] = -1

        try:
            from prax.services.memory.vector_store import get_user_memory_count
            result["vector_memories"] = get_user_memory_count(user_id)
        except Exception:
            result["vector_memories"] = -1

        try:
            from prax.services.memory.graph_store import get_stats
            result.update(graph_store_stats=get_stats(user_id))
        except Exception:
            result["graph_store_stats"] = {"entities": -1, "relations": -1}

        return result

    # ------------------------------------------------------------------
    # Context injection — for orchestrator prompt assembly
    # ------------------------------------------------------------------

    def build_memory_context(self, user_id: str, user_input: str, max_tokens: int = 500) -> str:
        """Retrieve relevant memories and format as system prompt context.

        Called by the orchestrator to inject memory into the system prompt.
        Budget-constrained to avoid bloating context.
        """
        parts: list[str] = []

        # STM scratchpad (always available, no infra dependency)
        try:
            from prax.services.memory.stm import stm_read
            stm_entries = stm_read(user_id)
            if stm_entries:
                parts.append("\n## Working Memory (Scratchpad)")
                for entry in stm_entries[-5:]:
                    parts.append(f"- **{entry.key}**: {entry.content[:200]}")
        except Exception:
            pass

        # LTM recall (requires Qdrant + optionally Neo4j)
        if self._available:
            try:
                memories = self.recall(user_id, user_input, top_k=5)
                if memories:
                    parts.append("\n## Relevant Memories")
                    for m in memories:
                        date = m.created_at[:10] if m.created_at else "?"
                        parts.append(f"- [{m.source}, {date}] {m.content[:200]}")
            except Exception:
                pass

        if not parts:
            return ""

        context = "\n".join(parts)
        # Rough token estimate: ~4 chars per token
        if len(context) > max_tokens * 4:
            context = context[: max_tokens * 4] + "\n...(truncated)"

        return context


def get_memory_service() -> MemoryService:
    """Return the singleton MemoryService instance."""
    global _instance
    if _instance is None:
        _instance = MemoryService()
    return _instance
