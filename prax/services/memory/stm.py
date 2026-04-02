"""Short-term memory store — per-user scratchpad backed by workspace JSON files.

STM provides fast, bounded, human-auditable working memory.  Entries are
stored in ``{workspace}/memory/stm.json``.  When the entry count exceeds
the configured maximum, the oldest entries are LLM-summarised into a single
compacted entry to keep the context budget bounded.

References:
  - Anthropic, "Effective context engineering for AI agents" (2025):
    structured note-taking as agentic memory.
  - MemGPT (Packer et al., 2023): writable working context block.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from prax.services.memory.models import STMEntry
from prax.settings import settings

logger = logging.getLogger(__name__)


def _stm_path(user_id: str) -> str:
    from prax.services.workspace_service import workspace_root

    root = workspace_root(user_id)
    mem_dir = os.path.join(root, "memory")
    os.makedirs(mem_dir, exist_ok=True)
    return os.path.join(mem_dir, "stm.json")


def _load(user_id: str) -> list[dict]:
    path = _stm_path(user_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupted STM file for %s — resetting", user_id)
        return []


def _save(user_id: str, entries: list[dict]) -> None:
    path = _stm_path(user_id)
    with open(path, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def stm_write(
    user_id: str,
    key: str,
    content: str,
    tags: list[str] | None = None,
    importance: float = 0.5,
) -> STMEntry:
    """Write or update a scratchpad entry.

    If an entry with the same *key* exists, its content is replaced and its
    access_count is incremented (reinforcement).
    """
    entries = _load(user_id)
    now = datetime.now(timezone.utc).isoformat()
    tags = tags or []

    # Upsert by key
    for e in entries:
        if e["key"] == key:
            e["content"] = content
            e["tags"] = tags
            e["importance"] = importance
            e["access_count"] = e.get("access_count", 0) + 1
            _save(user_id, entries)
            return STMEntry(**e)

    entry = {
        "key": key,
        "content": content,
        "tags": tags,
        "created_at": now,
        "access_count": 0,
        "importance": importance,
    }
    entries.append(entry)
    _save(user_id, entries)
    return STMEntry(**entry)


def stm_read(user_id: str, key: str | None = None) -> list[STMEntry]:
    """Read scratchpad entries.  If *key* is given, return only that entry."""
    entries = _load(user_id)
    if key:
        entries = [e for e in entries if e["key"] == key]
    return [STMEntry(**e) for e in entries]


def stm_delete(user_id: str, key: str) -> bool:
    """Delete a scratchpad entry by key."""
    entries = _load(user_id)
    before = len(entries)
    entries = [e for e in entries if e["key"] != key]
    if len(entries) < before:
        _save(user_id, entries)
        return True
    return False


def stm_compact(user_id: str) -> str:
    """Summarise old entries via LLM and replace them with a single summary.

    Keeps the most recent ``max_keep`` entries intact and compacts the rest.
    Returns the summary text.
    """
    entries = _load(user_id)
    max_entries = getattr(settings, "memory_stm_max_entries", 50)
    if len(entries) <= max_entries:
        return "(no compaction needed)"

    # Keep the newest half, compact the oldest half
    split = len(entries) // 2
    to_compact = entries[:split]
    to_keep = entries[split:]

    # Build text for LLM summarisation
    text_parts = []
    for e in to_compact:
        text_parts.append(f"[{e['key']}] {e['content']}")
    text_blob = "\n\n".join(text_parts)

    try:
        from prax.agent.llm_factory import build_llm

        llm = build_llm(config_key="memory_compact", default_tier="low")
        from langchain_core.messages import HumanMessage, SystemMessage

        msgs = [
            SystemMessage(
                content=(
                    "You are a memory compaction assistant.  Summarise the "
                    "following working notes into a single concise summary that "
                    "preserves all important facts, preferences, and context.  "
                    "Output only the summary text."
                )
            ),
            HumanMessage(content=text_blob),
        ]
        result = llm.invoke(msgs)
        summary_text = result.content if hasattr(result, "content") else str(result)
    except Exception:
        logger.exception("LLM compaction failed — falling back to concatenation")
        summary_text = text_blob[:2000]

    now = datetime.now(timezone.utc).isoformat()
    summary_entry = {
        "key": "_compacted_summary",
        "content": summary_text,
        "tags": ["compacted"],
        "created_at": now,
        "access_count": 0,
        "importance": 0.7,
    }

    _save(user_id, [summary_entry] + to_keep)
    logger.info(
        "STM compacted for %s: %d entries → 1 summary + %d kept",
        user_id,
        len(to_compact),
        len(to_keep),
    )
    return summary_text
