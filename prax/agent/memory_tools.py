"""Memory tools — agent-facing tools for short-term and long-term memory.

These tools are used by the memory spoke agent (and can also be used
directly by the orchestrator for simple operations).
"""
from __future__ import annotations

from langchain_core.tools import tool

from prax.agent.user_context import current_user_id


def _uid() -> str:
    uid = current_user_id.get()
    if not uid:
        return "anonymous"
    return uid


# ---------------------------------------------------------------------------
# Short-term memory (scratchpad)
# ---------------------------------------------------------------------------


@tool
def memory_stm_write(key: str, content: str, importance: float = 0.5) -> str:
    """Write to short-term working memory (scratchpad).

    Use this to save facts, context, or notes that should persist within
    and across conversations.  Each entry has a unique key — writing to
    an existing key updates it.

    Args:
        key: Short identifier (e.g., "user_timezone", "current_project").
        content: The information to remember.
        importance: 0-1 score (higher = more important, less likely to be compacted).
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    entry = svc.stm_write(_uid(), key, content, importance=importance)
    return f"Saved to scratchpad: [{entry.key}] {entry.content[:100]}"


@tool
def memory_stm_read(key: str = "") -> str:
    """Read short-term memory entries.

    Pass a key to read a specific entry, or leave empty to read all entries.

    Args:
        key: Optional key to filter by.  Empty string returns all entries.
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    entries = svc.stm_read(_uid(), key=key if key else None)
    if not entries:
        return "No scratchpad entries found." if not key else f"No entry with key '{key}'."

    lines = []
    for e in entries:
        lines.append(f"[{e.key}] (importance={e.importance:.1f}) {e.content}")
    return "\n".join(lines)


@tool
def memory_stm_delete(key: str) -> str:
    """Delete a short-term memory entry by key.

    Args:
        key: The entry key to delete.
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    if svc.stm_delete(_uid(), key):
        return f"Deleted scratchpad entry: {key}"
    return f"No entry found with key: {key}"


# ---------------------------------------------------------------------------
# Long-term memory — remember and recall
# ---------------------------------------------------------------------------


@tool
def memory_remember(
    content: str,
    importance: float = 0.5,
    tags: str = "",
) -> str:
    """Store an important fact, preference, or insight in long-term memory.

    This creates a durable memory that can be recalled later via semantic
    search.  Use for things worth remembering across conversations:
    preferences, decisions, key facts, insights.

    Args:
        content: The information to remember (be specific and self-contained).
        importance: 0-1 score.  0.8+ for critical facts, 0.5 for useful context.
        tags: Comma-separated tags for organisation (e.g., "preference,coding").
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    if not svc.available:
        return "Memory system not available (MEMORY_ENABLED=false or infrastructure down)."

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    mid = svc.remember(_uid(), content, importance=importance, tags=tag_list)
    if mid:
        return f"Remembered (id={mid[:8]}...): {content[:100]}"
    return "Failed to store memory."


@tool
def memory_recall(query: str, top_k: int = 5) -> str:
    """Search long-term memory by semantic similarity.

    Returns the most relevant memories matching the query, ranked by
    a combination of semantic relevance, recency, and importance.

    Args:
        query: What to search for (natural language).
        top_k: Maximum number of results to return.
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    if not svc.available:
        return "Memory system not available."

    results = svc.recall(_uid(), query, top_k=min(top_k, 20))
    if not results:
        return "No relevant memories found."

    lines = []
    for r in results:
        date = r.created_at[:10] if r.created_at else "?"
        entities = f" entities=[{','.join(r.entities[:3])}]" if r.entities else ""
        lines.append(
            f"- [{r.source}, {date}, imp={r.importance:.1f}]{entities}\n  {r.content}"
        )
    return "\n".join(lines)


@tool
def memory_forget(memory_id: str) -> str:
    """Delete a specific memory by ID.

    Use this when a memory is outdated or incorrect.

    Args:
        memory_id: The memory ID (from memory_recall results).
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    if svc.forget(_uid(), memory_id):
        return f"Forgot memory {memory_id}."
    return f"Failed to forget memory {memory_id}."


# ---------------------------------------------------------------------------
# Graph / entity operations
# ---------------------------------------------------------------------------


@tool
def memory_entity_lookup(entity_name: str) -> str:
    """Look up everything known about an entity (person, topic, project, etc.).

    Returns the entity details and all its relationships in the knowledge graph.

    Args:
        entity_name: Name of the entity to look up.
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    if not svc.available:
        return "Memory system not available."

    entity = svc.entity_lookup(_uid(), entity_name)
    if not entity:
        return f"No entity found matching '{entity_name}'."

    parts = [
        f"**{entity.display_name}** ({entity.entity_type})",
        f"Importance: {entity.importance:.2f} | Mentions: {entity.mention_count}",
        f"First seen: {entity.first_seen[:10] if entity.first_seen else '?'} | "
        f"Last seen: {entity.last_seen[:10] if entity.last_seen else '?'}",
    ]

    if entity.relations:
        parts.append("\nRelationships:")
        for rel in entity.relations[:15]:
            direction = rel.get("direction", "")
            arrow = "→" if direction == "outgoing" else "←"
            parts.append(
                f"  {arrow} {rel.get('type', '?')} **{rel.get('other_name', '?')}** "
                f"({rel.get('other_type', '?')}, weight={rel.get('weight', 0):.1f})"
            )

    return "\n".join(parts)


@tool
def memory_graph_query(question: str) -> str:
    """Query the knowledge graph for structured relationships.

    Use this for questions about connections between entities:
    "What topics are related to X?", "What does the user work on?",
    "How are A and B connected?"

    Args:
        question: Natural language question about relationships.
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    return svc.graph_query(_uid(), question)


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


@tool
def memory_consolidate() -> str:
    """Trigger memory consolidation from recent conversations.

    This extracts entities, relations, and key facts from recent
    conversation traces and stores them in long-term memory.  Usually
    runs automatically, but can be triggered manually.
    """
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    result = svc.consolidate(_uid())
    return (
        f"Consolidation complete: {result.memories_created} memories, "
        f"{result.entities_upserted} entities, {result.relations_upserted} relations. "
        f"Decayed: {result.memories_decayed} memories, {result.memories_forgotten} graph nodes."
        + (f"\nDaily summary: {result.daily_summary[:200]}" if result.daily_summary else "")
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@tool
def memory_stats() -> str:
    """Show memory system statistics — entry counts, health, storage usage."""
    from prax.services.memory_service import get_memory_service

    svc = get_memory_service()
    stats = svc.stats(_uid())

    lines = [f"Memory enabled: {stats.get('memory_enabled', False)}"]
    if stats.get("memory_enabled"):
        lines.append(f"STM entries: {stats.get('stm_entries', '?')}")
        lines.append(f"Vector memories: {stats.get('vector_memories', '?')}")
        gs = stats.get("graph_store_stats", {})
        lines.append(f"Graph entities: {gs.get('entities', '?')}")
        lines.append(f"Graph relations: {gs.get('relations', '?')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structured durable memory ledger
# ---------------------------------------------------------------------------


def _csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@tool
def memory_structured_record(
    bucket: str,
    key: str,
    content: str,
    scope: str = "user",
    source: str = "",
    confidence: float = 0.7,
    importance: float = 0.5,
    tags: str = "",
    ttl_days: int | None = None,
    supersedes: str = "",
) -> str:
    """Record typed durable memory with scope and lifecycle metadata.

    Buckets: preference, project_fact, session_scratchpad, decision, tool_note.
    Scopes: user, project, session.

    Use this when the memory should be inspectable and explicitly typed rather
    than only embedded in vector/graph memory.
    """
    from prax.services import structured_memory_service as structured

    try:
        record = structured.record_memory(
            _uid(),
            bucket=bucket,
            key=key,
            content=content,
            scope=scope,
            source=source,
            confidence=confidence,
            importance=importance,
            tags=_csv(tags),
            ttl_days=ttl_days,
            supersedes=supersedes,
        )
    except ValueError as exc:
        return f"Failed to record structured memory: {exc}"
    return (
        f"Structured memory saved: id={record['id']} "
        f"bucket={record['bucket']} scope={record['scope']} key={record['key']}"
    )


@tool
def memory_structured_find(
    query: str = "",
    bucket: str = "",
    scope: str = "",
    status: str = "active",
    limit: int = 20,
) -> str:
    """Find typed durable memory records by query, bucket, scope, and status."""
    from prax.services import structured_memory_service as structured

    records = structured.list_memories(
        _uid(),
        query=query,
        bucket=bucket,
        scope=scope,
        status=status,
        limit=limit,
    )
    if not records:
        return "No structured memory records found."

    lines = []
    for record in records:
        tags = f" tags={','.join(record.get('tags', []))}" if record.get("tags") else ""
        lines.append(
            f"- {record['id']} [{record['bucket']}/{record['scope']}/{record['status']}] "
            f"imp={record.get('importance', 0):.1f} conf={record.get('confidence', 0):.1f}{tags}\n"
            f"  {record['key']}: {record['content']}"
        )
    return "\n".join(lines)


@tool
def memory_structured_archive(memory_id: str, reason: str = "") -> str:
    """Archive a typed durable memory record by id."""
    from prax.services import structured_memory_service as structured

    record = structured.archive_memory(_uid(), memory_id, reason=reason)
    if not record:
        return f"No structured memory found with id={memory_id}."
    return f"Archived structured memory {memory_id}: {record['key']}"


# ---------------------------------------------------------------------------
# Tool builders
# ---------------------------------------------------------------------------


def build_memory_tools() -> list:
    """Return all memory tools for spoke agent use."""
    return [
        memory_stm_write,
        memory_stm_read,
        memory_stm_delete,
        memory_remember,
        memory_recall,
        memory_forget,
        memory_entity_lookup,
        memory_graph_query,
        memory_consolidate,
        memory_stats,
        memory_structured_record,
        memory_structured_find,
        memory_structured_archive,
    ]
