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
    entities_upserted: int = 0
    relations_upserted: int = 0
    memories_decayed: int = 0
    memories_forgotten: int = 0
    daily_summary: str = ""


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
