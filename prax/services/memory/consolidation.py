"""Memory consolidation service — converts episodic traces into durable LTM.

Pipeline:
  1. Read unconsolidated conversation entries from the trace log in
     character-budgeted batches — up to `MAX_BATCHES_PER_RUN` per run, the
     pointer advancing past exactly what was handed to the extractor (see
     `_read_unconsolidated`).  Whatever is left is reported as the pending
     backlog; it is picked up by later runs UNLESS trace.log rotates first,
     in which case it is dropped and the drop is logged (`_record_rotation`)
  2. LLM extraction of entities, relations, key facts, temporal events, causal links
  3. Validation gate: filter by extraction confidence (≥0.6 threshold)
  4. Score importance
  5. Upsert entities/relations to graph (merge semantics, bi-temporal edges)
  6. Upsert temporal events and causal links (multi-graph layers)
  7. Chunk and embed text into vector store
  8. Time-based decay/prune pass — at most once per `DECAY_MIN_INTERVAL`
     (gated on `last_decay_run` in the consolidation state)
  9. Build/update daily summary
  10. Low-confidence facts → STM pending review
  11. Mark entries as consolidated

Triggered by:
  - Event-based (after N conversation turns, `memory_service.maybe_consolidate`)
  - Manual (via memory_consolidate tool)

References:
  - Park et al., "Generative Agents" (2023): reflection + importance scoring.
  - Zhong et al., "MemoryBank" (2023): daily summaries + forgetting curve.
  - Anthropic (2025): compaction and structured note-taking.
  - Harvard et al. (2025, arXiv:2505.16067): error propagation from bad memories.
  - Jiang et al., "MAGMA" (2026): multi-graph temporal + causal layers.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from prax.services.memory.models import ConsolidationResult
from prax.settings import settings

logger = logging.getLogger(__name__)

# Minimum confidence for LLM-extracted facts/entities to be committed to LTM.
# Below this threshold, facts go to STM as "pending_review" for human validation.
# Protects against error propagation (Harvard et al., 2025, arXiv:2505.16067).
CONFIDENCE_THRESHOLD = 0.6

# How much trace text ONE extraction call (and the daily summary) sees, in
# characters.  `_extract_entities_relations` and `_build_daily_summary` cap
# their prompt at this, so the batch builder must never hand them more: the
# consolidation pointer advances past whatever was batched, and anything
# batched-but-truncated is never seen again.  Until 2026-09 the batch was "50
# lines" (up to ~250 KB of trace) while the extractor read `text[:4000]` —
# the pointer skipped past the other 98%.
EXTRACTION_CHAR_BUDGET = 4000

# How many extractor batches ONE consolidate_user call may drain.  A turn
# writes several trace lines of up to 5 000 characters and consolidation runs
# every 5 turns, so a single 4 000-char batch per run fell further behind every
# cycle and each 512 KB rotation then discarded the whole backlog of the old
# file.  Eight batches (≤ 32 KB of extraction, eight low-tier LLM calls) per
# run bounds the cost; a trace that fits in one batch behaves exactly as
# before.  What is left over is `pending_lines` / `pending_bytes` on the
# result and in the state file — visible lag, not silent loss, until rotation.
MAX_BATCHES_PER_RUN = 8

# The decay/prune pass measures elapsed time from each memory's last access,
# so running it more often than this buys nothing; running it on every
# consolidation (every 5 turns) was what let importance compound before the
# pass was made idempotent.  Gated on `last_decay_run` in the state file.
DECAY_MIN_INTERVAL = timedelta(hours=24)

# Only the first non-blank line of trace.log is fingerprinted; it is enough to
# tell one file from its successor (rotation writes a timestamped header).
_TRACE_HEAD_CHARS = 200


def _consolidation_state_path(user_id: str) -> str:
    from prax.services.workspace_service import workspace_root

    root = workspace_root(user_id)
    mem_dir = os.path.join(root, "memory")
    os.makedirs(mem_dir, exist_ok=True)
    return os.path.join(mem_dir, "consolidation_state.json")


def _default_state() -> dict:
    return {
        "last_consolidated_line": 0,
        "last_daily_summary": "",
        "last_decay_run": "",
        # Fingerprint of the trace file the pointer refers to, so a rotated
        # or replaced trace.log is recognised even when the new file has
        # already grown past the old pointer.
        "trace_head": None,
        "rotation_resets": 0,
        "last_rotation_reset_at": "",
        # Size of trace.log and the unconsolidated backlog past the pointer as
        # of the last run, so a rotation can say how much it dropped.  None
        # until a run written by code that tracks it.
        "trace_total_lines": None,
        "trace_pending_lines": 0,
        "trace_pending_bytes": 0,
        # Content lines of the previous file that the last rotation reset
        # abandoned (as of the run before it; lines appended later are extra).
        "last_rotation_dropped_lines": 0,
    }


def _load_state(user_id: str) -> dict:
    path = _consolidation_state_path(user_id)
    state = _default_state()
    if os.path.exists(path):
        try:
            with open(path) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state.update(loaded)
        except (json.JSONDecodeError, OSError):
            pass
    return state


def _save_state(user_id: str, state: dict) -> None:
    # Same-directory temp file + os.replace: a crash mid-write leaves the
    # previous state intact instead of a truncated JSON that `_load_state`
    # would silently reset to "line 0" (re-consolidating the whole trace).
    from prax.services.workspace_service import atomic_write

    atomic_write(_consolidation_state_path(user_id), json.dumps(state, indent=2))


@dataclass
class _Batch:
    """One extractor-sized slice of the trace log, plus pointer bookkeeping."""

    lines: list[str] = field(default_factory=list)
    text: str = ""
    # Raw line index the pointer must advance to: one past the last line the
    # extractor was actually handed (blank lines passed over on the way are
    # included — they carry no content).
    next_line: int = 0
    # First non-blank line of the file as read (None if the file is empty).
    head: str | None = None
    # True when the file on disk is not the one the pointer was tracking.
    rotated: bool = False
    bytes_seen: int = 0
    bytes_skipped: int = 0
    # Raw line count of the file as read, and the non-blank lines (with their
    # UTF-8 size) that remain past `next_line` — the backlog after this batch.
    total_lines: int = 0
    pending_lines: int = 0
    pending_bytes: int = 0


def _read_unconsolidated(user_id: str, state: dict) -> _Batch:
    """Read the next batch of trace entries the extractor has not seen.

    # INVARIANT: `next_line` is one past the last line whose content is in
    # `text`.  The pointer used to count RAW lines on the way in and NON-BLANK
    # lines on the way out (`len(batch)`), so every blank separator in a batch
    # left the pointer short by one and those entries were consolidated again
    # next run — and after trace.log was rotated at 512 KB the pointer pointed
    # past the end of the new, small file, so nothing was consolidated at all.

    Rotation/replacement is detected two ways: the file has fewer lines than
    the pointer, or its first non-blank line no longer matches the recorded
    fingerprint.  Either resets the pointer to 0 (and the caller records it).

    The batch is bounded by `EXTRACTION_CHAR_BUDGET` characters, not by a line
    count, because that is the bound the extractor actually enforces.  A
    single line longer than the budget is sent alone, truncated, and the
    truncated tail is reported in `bytes_skipped` rather than silently lost.
    """
    from prax.services.workspace_service import workspace_root

    root = workspace_root(user_id)
    trace_path = os.path.join(root, "trace.log")
    batch = _Batch()
    if not os.path.exists(trace_path):
        return batch

    # errors="replace": one undecodable byte must not stall the pointer forever.
    with open(trace_path, encoding="utf-8", errors="replace") as f:
        raw = f.readlines()

    pointer = int(state.get("last_consolidated_line", 0) or 0)
    batch.head = next(
        (ln.strip()[:_TRACE_HEAD_CHARS] for ln in raw if ln.strip()), None
    )
    known_head = state.get("trace_head")
    if pointer > len(raw) or (
        known_head is not None and batch.head is not None and batch.head != known_head
    ):
        batch.rotated = True
        pointer = 0
    batch.next_line = pointer

    used = 0
    for i in range(pointer, len(raw)):
        stripped = raw[i].strip()
        if not stripped:
            batch.next_line = i + 1
            continue
        if batch.lines:
            if used + 1 + len(stripped) > EXTRACTION_CHAR_BUDGET:
                break
            used += 1 + len(stripped)
        else:
            if len(stripped) > EXTRACTION_CHAR_BUDGET:
                tail = stripped[EXTRACTION_CHAR_BUDGET:]
                batch.bytes_skipped += len(tail.encode("utf-8"))
                stripped = stripped[:EXTRACTION_CHAR_BUDGET]
            used = len(stripped)
        batch.lines.append(stripped)
        batch.next_line = i + 1

    batch.text = "\n".join(batch.lines)
    batch.bytes_seen = len(batch.text.encode("utf-8"))
    batch.total_lines = len(raw)
    for j in range(batch.next_line, len(raw)):
        stripped = raw[j].strip()
        if stripped:
            batch.pending_lines += 1
            batch.pending_bytes += len(stripped.encode("utf-8"))
    return batch


def _commit_pointer(state: dict, batch: _Batch) -> None:
    """Record where the extractor got to and how much is still waiting."""
    state["last_consolidated_line"] = batch.next_line
    state["trace_head"] = batch.head
    state["trace_total_lines"] = batch.total_lines
    state["trace_pending_lines"] = batch.pending_lines
    state["trace_pending_bytes"] = batch.pending_bytes


def _record_rotation(user_id: str, state: dict, batch: _Batch) -> None:
    """Log a pointer reset — and what it abandons.

    A rotation moves trace.log to archive/trace_logs/, which consolidation
    never reads, so every content line of the old file past the pointer is
    lost to long-term memory.  The state file knows the backlog as of the last
    run; lines appended between that run and the rotation are lost too and
    uncounted, so the figure is a floor.
    """
    old_pointer = int(state.get("last_consolidated_line", 0) or 0)
    old_total = state.get("trace_total_lines")
    dropped = int(state.get("trace_pending_lines", 0) or 0)
    state["rotation_resets"] = int(state.get("rotation_resets", 0) or 0) + 1
    state["last_rotation_reset_at"] = datetime.now(UTC).isoformat()
    state["last_rotation_dropped_lines"] = dropped
    if old_total is None:
        # State written before the backlog was tracked — the size of the loss
        # is unknown, which is itself worth saying.
        logger.warning(
            "trace.log for %s was rotated or replaced — consolidation pointer reset "
            "from line %d to 0 (reset #%d); the old file's unconsolidated backlog was "
            "not tracked in this state file, so how many lines were never consolidated "
            "is unknown (the archived copy is not read by consolidation)",
            user_id, old_pointer, state["rotation_resets"],
        )
        return
    logger.warning(
        "trace.log for %s was rotated or replaced — consolidation pointer reset "
        "from line %d to 0 (reset #%d). At the last run the old file had %d lines "
        "with %d content lines (%d bytes) past the pointer; those lines, plus "
        "anything appended since, were never consolidated and will not be (the "
        "archived copy under archive/trace_logs/ is not read by consolidation)",
        user_id, old_pointer, state["rotation_resets"], int(old_total), dropped,
        int(state.get("trace_pending_bytes", 0) or 0),
    )


def _decay_due(state: dict, now: datetime) -> bool:
    """True when the last decay pass is older than `DECAY_MIN_INTERVAL` (or unknown)."""
    last = state.get("last_decay_run") or ""
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=UTC)
    return now - last_dt >= DECAY_MIN_INTERVAL


def consolidate_user(user_id: str) -> ConsolidationResult:
    """Full consolidation pipeline for one user.

    Drains up to `MAX_BATCHES_PER_RUN` extractor batches from the trace, saving
    the pointer after each so a crash mid-run loses at most one batch of work
    and never re-sends what was already extracted.  The decay pass and the
    daily summary run once per call, after the batches.
    """
    result = ConsolidationResult()
    state = _load_state(user_id)

    # 1. Read and consolidate batches until the trace is drained or the
    #    per-run cap is hit.  Each batch: extract -> validate -> upsert (2-9).
    first_text: str | None = None
    low_conf_total = 0
    batch = _read_unconsolidated(user_id, state)
    while True:
        if batch.rotated:
            _record_rotation(user_id, state, batch)
        if not batch.lines:
            if batch.rotated:
                _commit_pointer(state, batch)
                _save_state(user_id, state)
            break
        result.batches += 1
        result.bytes_seen += batch.bytes_seen
        result.bytes_skipped += batch.bytes_skipped
        if first_text is None:
            first_text = batch.text
        low_conf_total += _consolidate_batch(user_id, batch.text, result)
        _commit_pointer(state, batch)
        _save_state(user_id, state)
        if result.batches >= MAX_BATCHES_PER_RUN:
            break
        batch = _read_unconsolidated(user_id, state)

    result.pending_lines = batch.pending_lines
    result.pending_bytes = batch.pending_bytes
    if first_text is None:
        logger.debug("No unconsolidated entries for user %s", user_id)
        return result
    text_blob = first_text

    from prax.services.memory import embedder, graph_store, vector_store

    # 10. Time-based decay/prune pass — at most once per DECAY_MIN_INTERVAL.
    #     `last_decay_run` was written on every consolidation and never read,
    #     so the pass ran every 5 turns; combined with the (since removed)
    #     write-back of decayed importance, a 7-day half-life pruned active
    #     users' memories in 4-8 days.  The pass itself is now idempotent for
    #     a given "now" (see vector_store.decay_memories), and this gate keeps
    #     the cadence at the daily one the docs always described.
    now = datetime.now(UTC)
    halflife = getattr(settings, "memory_decay_halflife_days", 7.0)
    if _decay_due(state, now):
        try:
            result.memories_decayed = vector_store.decay_memories(user_id, halflife_days=halflife)
            result.memories_forgotten = graph_store.decay_graph(user_id, halflife_days=halflife * 2)
            state["last_decay_run"] = now.isoformat()
        except Exception:
            logger.debug("Decay pass failed", exc_info=True)

    # 11. Build daily summary
    today = now.strftime("%Y-%m-%d")
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

    # 12. Persist the decay / daily-summary marks (the pointer was saved after
    #     each batch above).
    _save_state(user_id, state)

    logger.info(
        "Consolidated for %s: %d memories (%d failed), %d entities, %d relations, "
        "%d pruned, %d low-conf→STM, %d batches, %d bytes seen (%d skipped), "
        "pointer→%d, backlog %d lines (%d bytes)",
        user_id,
        result.memories_created,
        result.memories_failed,
        result.entities_upserted,
        result.relations_upserted,
        result.memories_decayed,
        low_conf_total,
        result.batches,
        result.bytes_seen,
        result.bytes_skipped,
        state["last_consolidated_line"],
        result.pending_lines,
        result.pending_bytes,
    )
    if result.pending_lines:
        logger.info(
            "Consolidation for %s hit MAX_BATCHES_PER_RUN=%d with %d content lines "
            "(%d bytes) still unconsolidated — picked up by later runs unless "
            "trace.log rotates first",
            user_id, MAX_BATCHES_PER_RUN, result.pending_lines, result.pending_bytes,
        )
    return result


def _consolidate_batch(user_id: str, text_blob: str, result: ConsolidationResult) -> int:
    """Steps 2-9 for one extractor batch: extract, gate by confidence, upsert.

    Mutates *result* in place; returns the number of low-confidence items
    parked in STM (for the run log).
    """
    # 2. Extract entities, relations, facts, temporal events, causal links via LLM
    extraction = _extract_entities_relations(text_blob)

    # 3. Validation gate — split by confidence
    facts = extraction.get("facts", [])
    entities = extraction.get("entities", [])
    relations = extraction.get("relations", [])
    temporal_events = extraction.get("temporal_events", [])
    causal_links = extraction.get("causal_links", [])

    # Filter entities by confidence
    high_conf_entities = [
        e for e in entities
        if e.get("confidence", 1.0) >= CONFIDENCE_THRESHOLD
    ]
    low_conf_entities = [
        e for e in entities
        if e.get("confidence", 1.0) < CONFIDENCE_THRESHOLD
    ]

    # Filter facts by confidence
    high_conf_facts = []
    low_conf_facts = []
    for fact in facts:
        if isinstance(fact, str):
            high_conf_facts.append(fact)
            continue
        conf = fact.get("confidence", 1.0)
        if conf >= CONFIDENCE_THRESHOLD:
            high_conf_facts.append(fact)
        else:
            low_conf_facts.append(fact)

    # 4. Upsert high-confidence entities to graph
    from prax.services.memory import consistency, graph_store

    for ent in high_conf_entities:
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

    # 5. Upsert relations to graph (bi-temporal edges)
    for rel in relations:
        # Only upsert if both endpoints were high-confidence
        conf = rel.get("confidence", 1.0)
        if conf < CONFIDENCE_THRESHOLD:
            continue
        try:
            # Symbolic consistency pass (flag-gated): for single-valued
            # relation types, ask the GRAPH whether a conflicting current
            # edge exists, instead of trusting the extractor to have
            # volunteered `supersedes`. Log-only unless auto-supersede is on.
            if consistency.enabled():
                consistency.enforce(user_id, rel, result)
            graph_store.add_relation(
                user_id=user_id,
                source_name=rel.get("source", ""),
                relation_type=rel.get("type", "related_to"),
                target_name=rel.get("target", ""),
                weight=rel.get("weight", 1.0),
                evidence=rel.get("evidence", ""),
                valid_from=rel.get("valid_from"),
            )
            result.relations_upserted += 1

            # Handle supersession: if this relation contradicts an existing one
            supersedes = rel.get("supersedes")
            if supersedes:
                # INVARIANT: valid_until records when the fact CEASED TO BE
                # TRUE, not when we found out. The extraction reports it when
                # the utterance carries it; None falls back to now, which is
                # honest for "we just learned of a change happening now" and
                # dishonest for a retrospective one — hence asking for it (#65).
                graph_store.supersede_relation(
                    user_id=user_id,
                    source_name=supersedes.get("source", rel.get("source", "")),
                    relation_type=supersedes.get("type", rel.get("type", "")),
                    target_name=supersedes.get("target", rel.get("target", "")),
                    valid_until=supersedes.get("valid_until"),
                )
        except Exception:
            logger.debug("Failed to upsert relation: %s", rel, exc_info=True)

    # 6. Upsert temporal events (multi-graph: temporal layer)
    for evt in temporal_events:
        try:
            graph_store.merge_temporal_event(
                user_id=user_id,
                description=evt.get("description", ""),
                occurred_at=evt.get("occurred_at"),
                importance=evt.get("importance", 0.5),
                participant_names=evt.get("participants", []),
            )
        except Exception:
            logger.debug("Failed to upsert temporal event: %s", evt, exc_info=True)

    # 7. Upsert causal links (multi-graph: causal layer)
    for cl in causal_links:
        try:
            graph_store.add_causal_link(
                user_id=user_id,
                cause_description=cl.get("cause", ""),
                effect_description=cl.get("effect", ""),
                cause_entity_names=cl.get("cause_entities", []),
                effect_entity_names=cl.get("effect_entities", []),
                importance=cl.get("importance", 0.5),
            )
        except Exception:
            logger.debug("Failed to upsert causal link: %s", cl, exc_info=True)

    # 8. Chunk high-confidence facts and embed into vector store
    from prax.services.memory import embedder, vector_store

    for fact in high_conf_facts:
        try:
            content = fact if isinstance(fact, str) else fact.get("content", "")
            importance = 0.5 if isinstance(fact, str) else fact.get("importance", 0.5)
            if not content or len(content) < 10:
                continue

            dense_vec = embedder.embed_text(content)
            sparse_vec = embedder.sparse_encode(content)

            # Link to extracted entities
            entity_names = [e.get("name", "").lower() for e in high_conf_entities]
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
            # `upsert_memory` raises on a failed write (it used to return an
            # id regardless), so this count is now the honest one.
            result.memories_failed += 1
            logger.warning("Failed to store fact during consolidation: %s", fact, exc_info=True)

    # 9. Low-confidence facts → STM as pending review
    if low_conf_facts or low_conf_entities:
        try:
            from prax.services.memory.stm import stm_write

            for fact in low_conf_facts:
                content = fact if isinstance(fact, str) else fact.get("content", "")
                if content and len(content) >= 10:
                    stm_write(
                        user_id,
                        f"pending_review_{hash(content) % 10000}",
                        content,
                        tags=["pending_review", "low_confidence"],
                        importance=0.3,
                    )
            for ent in low_conf_entities:
                stm_write(
                    user_id,
                    f"pending_entity_{ent.get('name', 'unknown')}",
                    f"Low-confidence entity: {ent.get('display_name', ent.get('name', ''))} ({ent.get('type', '?')})",
                    tags=["pending_review", "low_confidence"],
                    importance=0.2,
                )
        except Exception:
            logger.debug("Failed to write low-confidence items to STM", exc_info=True)

    return len(low_conf_facts) + len(low_conf_entities)


def _consistency_addendum() -> str:
    """Extra prompt line teaching the single-valued relation types.

    Only when the consistency flag is on — with it off, the extraction prompt
    is byte-identical to prior behaviour.
    """
    from prax.services.memory import consistency

    if not consistency.enabled():
        return ""
    return "\n" + consistency.PROMPT_ADDENDUM


def _extract_entities_relations(text: str) -> dict:
    """Use LLM to extract structured entities, relations, and facts from text.

    Returns: {"entities": [...], "relations": [...], "facts": [...]}
    """
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from prax.agent.llm_factory import build_llm

        llm = build_llm(config_key="memory_consolidation", default_tier="low")

        msgs = [
            SystemMessage(
                content="""\
You are a memory extraction assistant. Given conversation traces, extract:

1. **entities** — people, topics, projects, tools, URLs, concepts, organisations
2. **relations** — connections between entities (who works on what, what relates to what)
3. **facts** — important statements worth remembering (preferences, decisions, insights)
4. **temporal_events** — discrete events that happened at a specific time
5. **causal_links** — cause-and-effect relationships ("X happened because Y")

Return JSON with this exact structure:
```json
{
  "entities": [
    {"name": "...", "display_name": "...", "type": "person|topic|project|tool|url|concept|organization", "importance": 0.0-1.0, "confidence": 0.0-1.0}
  ],
  "relations": [
    {"source": "entity_name", "type": "works_on|interested_in|prefers|related_to|part_of|caused_by|mentioned_with", "target": "entity_name", "weight": 1.0, "evidence": "brief reason", "confidence": 0.0-1.0, "valid_from": "ISO date or null", "supersedes": null}
    // "supersedes" replaces an older relation: {"source": "...", "type": "...", "target": "...", "valid_until": "ISO date or null"}
    // valid_until is WHEN THE OLD FACT STOPPED BEING TRUE, which is often NOT today.
    // "I moved to Berlin back in March" told in August => valid_until is March.
    // Use null only when the change genuinely happened just now.
  ],
  "facts": [
    {"content": "The important fact or preference to remember", "importance": 0.0-1.0, "confidence": 0.0-1.0}
  ],
  "temporal_events": [
    {"description": "What happened", "occurred_at": "ISO date or null", "importance": 0.0-1.0, "participants": ["entity_name"]}
  ],
  "causal_links": [
    {"cause": "Why it happened", "effect": "What resulted", "cause_entities": ["entity_name"], "effect_entities": ["entity_name"], "importance": 0.0-1.0}
  ]
}
```

Rules:
- Only extract genuinely important, durable information
- Skip transient details (greetings, confirmations, debug output)
- Importance 0.8-1.0: core preferences, key decisions, critical facts
- Importance 0.4-0.7: useful context, recurring topics
- Importance 0.1-0.3: minor mentions, tangential info
- **confidence** is how certain you are this extraction is correct (1.0=certain, 0.5=unsure)
- If a new relation contradicts an existing one (e.g., preference change), set "supersedes": {"source": "...", "type": "...", "target": "..."} on the new relation
- Entity names should be canonical (lowercase, no articles)
- temporal_events and causal_links can be empty arrays if none are present
- Return ONLY valid JSON, no commentary"""
                + _consistency_addendum()
            ),
            HumanMessage(
                content="Extract entities, relations, and facts from:\n\n"
                + text[:EXTRACTION_CHAR_BUDGET]
            ),
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
        from langchain_core.messages import HumanMessage, SystemMessage

        from prax.agent.llm_factory import build_llm

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
            HumanMessage(content=text[:EXTRACTION_CHAR_BUDGET]),
        ]
        result = llm.invoke(msgs)
        return result.content if hasattr(result, "content") else str(result)
    except Exception:
        logger.exception("Daily summary generation failed")
        return ""
