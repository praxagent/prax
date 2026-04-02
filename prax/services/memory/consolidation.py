"""Memory consolidation service — converts episodic traces into durable LTM.

Pipeline:
  1. Read unconsolidated conversation entries from the trace log
  2. LLM extraction of entities, relations, and key facts
  3. Score importance
  4. Upsert entities/relations to graph (merge semantics)
  5. Chunk and embed text into vector store
  6. Apply decay to old memories
  7. Build/update daily summary
  8. Mark entries as consolidated

Triggered by:
  - Scheduled job (hourly by default)
  - Event-based (after N conversation turns)
  - Manual (via memory_consolidate tool)

References:
  - Park et al., "Generative Agents" (2023): reflection + importance scoring.
  - Zhong et al., "MemoryBank" (2023): daily summaries + forgetting curve.
  - Anthropic (2025): compaction and structured note-taking.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from prax.services.memory.models import ConsolidationResult
from prax.settings import settings

logger = logging.getLogger(__name__)


def _consolidation_state_path(user_id: str) -> str:
    from prax.services.workspace_service import workspace_root

    root = workspace_root(user_id)
    mem_dir = os.path.join(root, "memory")
    os.makedirs(mem_dir, exist_ok=True)
    return os.path.join(mem_dir, "consolidation_state.json")


def _load_state(user_id: str) -> dict:
    path = _consolidation_state_path(user_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_consolidated_line": 0, "last_daily_summary": "", "last_decay_run": ""}


def _save_state(user_id: str, state: dict) -> None:
    path = _consolidation_state_path(user_id)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def _read_unconsolidated(user_id: str, state: dict) -> list[str]:
    """Read trace log entries that haven't been consolidated yet."""
    from prax.services.workspace_service import workspace_root

    root = workspace_root(user_id)
    trace_path = os.path.join(root, "trace.log")
    if not os.path.exists(trace_path):
        return []

    last_line = state.get("last_consolidated_line", 0)
    lines: list[str] = []
    with open(trace_path) as f:
        for i, line in enumerate(f):
            if i >= last_line:
                stripped = line.strip()
                if stripped:
                    lines.append(stripped)
    return lines


def consolidate_user(user_id: str) -> ConsolidationResult:
    """Full consolidation pipeline for one user."""
    result = ConsolidationResult()
    state = _load_state(user_id)

    # 1. Read unconsolidated entries
    lines = _read_unconsolidated(user_id, state)
    if not lines:
        logger.debug("No unconsolidated entries for user %s", user_id)
        return result

    # Batch into chunks to avoid overwhelming the LLM
    max_batch = 50
    batch = lines[:max_batch]
    text_blob = "\n".join(batch)

    # 2. Extract entities, relations, and facts via LLM
    extraction = _extract_entities_relations(text_blob)

    # 3. Score importance
    facts = extraction.get("facts", [])
    entities = extraction.get("entities", [])
    relations = extraction.get("relations", [])

    # 4. Upsert entities to graph
    from prax.services.memory import graph_store

    for ent in entities:
        try:
            graph_store.merge_entity(
                user_id=user_id,
                name=ent.get("name", ""),
                entity_type=ent.get("type", "concept"),
                display_name=ent.get("display_name"),
                importance=ent.get("importance", 0.5),
            )
            result.entities_upserted += 1
        except Exception:
            logger.debug("Failed to upsert entity: %s", ent, exc_info=True)

    # 5. Upsert relations to graph
    for rel in relations:
        try:
            graph_store.add_relation(
                user_id=user_id,
                source_name=rel.get("source", ""),
                relation_type=rel.get("type", "related_to"),
                target_name=rel.get("target", ""),
                weight=rel.get("weight", 1.0),
                evidence=rel.get("evidence", ""),
            )
            result.relations_upserted += 1
        except Exception:
            logger.debug("Failed to upsert relation: %s", rel, exc_info=True)

    # 6. Chunk facts and embed into vector store
    from prax.services.memory import embedder, vector_store

    for fact in facts:
        try:
            content = fact if isinstance(fact, str) else fact.get("content", "")
            importance = 0.5 if isinstance(fact, str) else fact.get("importance", 0.5)
            if not content or len(content) < 10:
                continue

            dense_vec = embedder.embed_text(content)
            sparse_vec = embedder.sparse_encode(content)

            # Link to extracted entities
            entity_names = [e.get("name", "").lower() for e in entities]
            linked = [n for n in entity_names if n in content.lower()]

            vector_store.upsert_memory(
                user_id=user_id,
                content=content,
                dense_vector=dense_vec,
                sparse_vector=sparse_vec,
                source="consolidation",
                importance=importance,
                entity_ids=linked,
            )
            result.memories_created += 1
        except Exception:
            logger.debug("Failed to store fact: %s", fact, exc_info=True)

    # 7. Apply decay
    halflife = getattr(settings, "memory_decay_halflife_days", 7.0)
    try:
        result.memories_decayed = vector_store.decay_memories(user_id, halflife_days=halflife)
        result.memories_forgotten = graph_store.decay_graph(user_id, halflife_days=halflife * 2)
    except Exception:
        logger.debug("Decay pass failed", exc_info=True)

    # 8. Build daily summary
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("last_daily_summary") != today:
        try:
            summary = _build_daily_summary(text_blob)
            result.daily_summary = summary

            # Store summary as a memory too
            dense_vec = embedder.embed_text(summary)
            sparse_vec = embedder.sparse_encode(summary)
            vector_store.upsert_memory(
                user_id=user_id,
                content=summary,
                dense_vector=dense_vec,
                sparse_vector=sparse_vec,
                source="consolidation",
                importance=0.7,
                summary_level="daily",
                tags=["daily_summary", today],
            )
            state["last_daily_summary"] = today
        except Exception:
            logger.debug("Daily summary failed", exc_info=True)

    # 9. Update state
    state["last_consolidated_line"] = state.get("last_consolidated_line", 0) + len(batch)
    state["last_decay_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(user_id, state)

    logger.info(
        "Consolidated for %s: %d memories, %d entities, %d relations, %d decayed",
        user_id,
        result.memories_created,
        result.entities_upserted,
        result.relations_upserted,
        result.memories_decayed,
    )
    return result


def _extract_entities_relations(text: str) -> dict:
    """Use LLM to extract structured entities, relations, and facts from text.

    Returns: {"entities": [...], "relations": [...], "facts": [...]}
    """
    try:
        from prax.agent.llm_factory import build_llm
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = build_llm(config_key="memory_consolidation", default_tier="low")

        msgs = [
            SystemMessage(
                content="""\
You are a memory extraction assistant. Given conversation traces, extract:

1. **entities** — people, topics, projects, tools, URLs, concepts, organisations
2. **relations** — connections between entities (who works on what, what relates to what)
3. **facts** — important statements worth remembering (preferences, decisions, insights)

Return JSON with this exact structure:
```json
{
  "entities": [
    {"name": "...", "display_name": "...", "type": "person|topic|project|tool|url|concept|organization", "importance": 0.0-1.0}
  ],
  "relations": [
    {"source": "entity_name", "type": "works_on|interested_in|prefers|related_to|part_of|caused_by|mentioned_with", "target": "entity_name", "weight": 1.0, "evidence": "brief reason"}
  ],
  "facts": [
    {"content": "The important fact or preference to remember", "importance": 0.0-1.0}
  ]
}
```

Rules:
- Only extract genuinely important, durable information
- Skip transient details (greetings, confirmations, debug output)
- Importance 0.8-1.0: core preferences, key decisions, critical facts
- Importance 0.4-0.7: useful context, recurring topics
- Importance 0.1-0.3: minor mentions, tangential info
- Entity names should be canonical (lowercase, no articles)
- Return ONLY valid JSON, no commentary"""
            ),
            HumanMessage(content=f"Extract entities, relations, and facts from:\n\n{text[:4000]}"),
        ]
        result = llm.invoke(msgs)
        content = result.content if hasattr(result, "content") else str(result)

        # Parse JSON from response (handle markdown code blocks)
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            if content.startswith("json"):
                content = content[4:].strip()

        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM extraction output as JSON")
        return {"entities": [], "relations": [], "facts": []}
    except Exception:
        logger.exception("Entity/relation extraction failed")
        return {"entities": [], "relations": [], "facts": []}


def _build_daily_summary(text: str) -> str:
    """Summarise a day's activity into a concise memory."""
    try:
        from prax.agent.llm_factory import build_llm
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = build_llm(config_key="memory_consolidation", default_tier="low")
        msgs = [
            SystemMessage(
                content=(
                    "Summarise the following conversation traces into a concise "
                    "daily summary (3-5 sentences).  Focus on: key decisions made, "
                    "tasks completed, preferences expressed, and unresolved items.  "
                    "Write in third person ('The user...').  Output only the summary."
                )
            ),
            HumanMessage(content=text[:4000]),
        ]
        result = llm.invoke(msgs)
        return result.content if hasattr(result, "content") else str(result)
    except Exception:
        logger.exception("Daily summary generation failed")
        return ""
