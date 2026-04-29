"""Memory spoke agent — manages short-term and long-term memory.

Prax delegates memory operations here: storing preferences, recalling past
conversations, querying the knowledge graph, and triggering consolidation.

The memory system has two layers:

**Short-term memory (STM):** A per-user scratchpad stored as workspace JSON.
Always available, no external infrastructure needed.  Used for facts and
context that should persist across conversation turns.

**Long-term memory (LTM):** Dual-store retrieval combining:
- Vector store (Qdrant) for semantic similarity search over memories
- Property graph (Neo4j) for structured entity/relation queries and
  multi-hop reasoning

Consolidation periodically extracts entities, relations, and key facts
from conversation traces and stores them in LTM with importance scoring
and Ebbinghaus-inspired decay.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from prax.agent.spokes._runner import run_spoke
from prax.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Memory Agent for {agent_name}.  You manage the two-layer memory
system: short-term scratchpad and long-term vector + graph memory.

## Short-Term Memory (Scratchpad)
Per-user working memory stored as workspace files.  Always available.

- **memory_stm_write** — Save a fact/note to the scratchpad (key + content).
- **memory_stm_read** — Read scratchpad entries (all or by key).
- **memory_stm_delete** — Remove a scratchpad entry.

## Long-Term Memory (requires MEMORY_ENABLED=true + Qdrant + Neo4j)
Durable memories with semantic search and knowledge graph.

- **memory_remember** — Store an important fact/preference in long-term memory.
- **memory_recall** — Search memories by semantic similarity (hybrid: dense + sparse + graph).
- **memory_forget** — Delete a specific memory.
- **memory_entity_lookup** — Look up an entity and all its relationships.
- **memory_graph_query** — Query the knowledge graph for structured relationships.
- **memory_consolidate** — Extract entities/relations from recent traces into LTM.
- **memory_stats** — Show memory system statistics.

## Structured Memory Ledger (always available)
Typed, inspectable JSON records in the user's git-backed workspace.

- **memory_structured_record** — Store a typed record with bucket, scope,
  confidence, importance, source, tags, and optional TTL/supersession.
- **memory_structured_find** — Search/filter structured records.
- **memory_structured_archive** — Retire stale or wrong structured records.

## Workflow
1. **Understand** what the user wants — store, recall, or explore memories.
2. **Execute** using the appropriate tool(s).
3. **Report** concisely — include the key information, not verbose confirmations.

## Knowledge Graph (separate from memory)
You also manage the Knowledge Graph — structured knowledge extracted from
documents, papers, and code. This is SEPARATE from conversational memory:

- **Memory graph**: facts about the user from conversations (Entity, Relation)
- **Knowledge graph**: concepts from documents/papers/code (KnowledgeConcept, KnowledgeDocument)

Use knowledge_ingest to extract concepts from uploaded documents.
Use knowledge_search to find concepts (NOT memory recall).
Use knowledge_namespaces to list available namespaces.
Use knowledge_connect to link a concept to a memory entity when relevant.

Namespaces keep knowledge organized. Don't dump everything into "general".

## Rules
- When asked "what do you know about X", use BOTH memory_recall (semantic) AND
  memory_entity_lookup (graph) to get a complete picture.  Also try
  knowledge_search in case relevant concepts exist in the knowledge graph.
- When storing memories, make content self-contained and specific.
- Use memory_structured_record for stable preferences, project facts,
  explicit decisions, and tool notes that should remain auditable.
- Set importance appropriately: 0.8+ for critical preferences/decisions,
  0.5 for useful context, 0.2 for minor notes.
- If LTM is unavailable, fall back to STM operations and inform the user.
- For "remember this" requests, use memory_remember (LTM) not just STM.
- For quick notes during a conversation, use memory_stm_write (STM).
- When ingesting documents, pick an appropriate namespace (papers, docs,
  codebase, uploads) — don't use "general" unless nothing else fits.
"""


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------


def build_tools() -> list:
    """Return all tools available to the memory spoke."""
    from prax.agent.knowledge_tools import build_knowledge_tools
    from prax.agent.memory_tools import build_memory_tools

    return build_memory_tools() + build_knowledge_tools()


# ---------------------------------------------------------------------------
# Delegation function
# ---------------------------------------------------------------------------


@tool
def delegate_memory(task: str) -> str:
    """Delegate a memory management task to the Memory Agent.

    The Memory Agent handles **facts about the user** — their preferences,
    context, history, and the knowledge graph OF the user's world. It is
    NOT for general knowledge questions about external topics.

    Use this ONLY for:
    - "Remember that I prefer dark mode" / "Store this preference"
    - "What do you remember about ME?" / "What do you know about MY project?"
    - "What topics have WE discussed?" (about the user's own conversations)
    - "What's connected to MY project Alpha?"
    - "Forget what you know about me"
    - "Save this user fact to my working notes"
    - "What's in my scratchpad?"
    - "Run memory consolidation"
    - "Show memory stats"

    Do NOT use this for:
    - **General knowledge questions** like "What are the latest findings on X?"
      or "What is a transformer?" — use delegate_research instead.
    - **System state queries** like "what plugins are installed?" or
      "check system status" — use delegate_sysadmin instead.
    - **Creating notes/pages** (use delegate_knowledge with note_create/note_deep_dive)
    - **Saving files to workspace** (use workspace_save)
    - **Searching conversation history** (use conversation_search)

    The distinguishing rule: delegate_memory is for "what does Prax know
    about ME/MY world" — not "what does Prax know about the world in general."

    Args:
        task: Description of the memory task.  Include the content to
              remember, the query to search for, or the entity to look up.
    """
    prompt = SYSTEM_PROMPT.format(agent_name=settings.agent_name)
    return run_spoke(
        task=task,
        system_prompt=prompt,
        tools=build_tools(),
        config_key="subagent_memory",
        role_name=None,
        channel=None,
        recursion_limit=20,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def build_spoke_tools() -> list:
    """Return the delegation tool for the main agent."""
    return [delegate_memory]
