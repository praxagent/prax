"""Data models for the memory subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryResult:
    """A single memory returned by search/recall."""

    memory_id: str
    content: str
    score: float  # fused relevance score (higher = better)
    source: str  # "conversation", "note", "stm", "consolidation"
    importance: float  # 0-1
    created_at: str  # ISO 8601
    entities: list[str] = field(default_factory=list)  # linked entity names
    metadata: dict = field(default_factory=dict)


@dataclass
class ConsolidationResult:
    """Outcome of a consolidation run for one user."""

    memories_created: int = 0
    # Vector writes that RAISED (upsert_memory no longer returns an id for a
    # failed write), so memories_created is a count of what actually landed.
    memories_failed: int = 0
    entities_upserted: int = 0
    relations_upserted: int = 0
    # Pruned from the vector store / graph by the time-based decay pass
    # (which runs at most once per 24 h, so these are 0 on most runs).
    memories_decayed: int = 0
    memories_forgotten: int = 0
    daily_summary: str = ""
    # UTF-8 bytes of trace text handed to the extractor this run, and bytes of
    # content the pointer advanced past WITHOUT the extractor seeing them (only
    # the tail of a single line longer than EXTRACTION_CHAR_BUDGET).
    bytes_seen: int = 0
    bytes_skipped: int = 0
    # Extractor batches drained this run (≤ consolidation.MAX_BATCHES_PER_RUN)
    # and the backlog left behind: non-blank trace lines past the pointer, with
    # their UTF-8 size.  A non-zero backlog means the run hit the cap; later
    # runs pick it up unless trace.log rotates first, in which case it is
    # dropped and logged at WARNING (consolidation._record_rotation).
    batches: int = 0
    pending_lines: int = 0
    pending_bytes: int = 0
    # Symbolic consistency pass (MEMORY_CONSISTENCY_ENABLED): conflicts a
    # single-valued relation write had with existing current edges, and how
    # many of those stale edges were closed (0 unless auto-supersede is on).
    conflicts_detected: int = 0
    conflicts_superseded: int = 0


@dataclass
class Entity:
    """An entity in the knowledge graph."""

    id: str
    name: str
    display_name: str
    entity_type: str  # person, topic, project, tool, url, concept, organization
    importance: float
    mention_count: int
    first_seen: str
    last_seen: str
    properties: dict = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)


@dataclass
class STMEntry:
    """A single short-term memory entry."""

    key: str
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    access_count: int = 0
    importance: float = 0.5
