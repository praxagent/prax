"""Neo4j-backed property graph for structured entity/relation memory.

Stores entities (people, topics, projects, concepts, tools) and typed
relations between them.  All queries are scoped by user_id.  Supports
multi-hop traversal and Personalized PageRank-style neighbourhood queries.

Gracefully degrades: if Neo4j is unreachable, operations return empty
results and log warnings.

Graph model (three node types):
  (:Entity {id, user_id, name, display_name, type, importance,
            first_seen, last_seen, mention_count, properties})
  (:TemporalEvent {id, user_id, description, occurred_at, importance})
  (:CausalLink   {id, user_id, description, importance})

Edge types:
  -[:RELATES_TO  {type, weight, first_seen, last_seen, evidence,
                  valid_from, valid_until}]->           (entity ↔ entity)
  -[:PARTICIPATED_IN]->   (entity → temporal event)
  -[:CAUSED_BY]->         (entity/event → causal link → entity/event)

Bi-temporal edges:
  Edges carry valid_from (when the fact became true) and valid_until
  (when it was superseded).  valid_until=null means "currently valid".
  Inspired by Rasmussen et al., "Zep" (2025): bi-temporal KG.

Multi-graph layers (MAGMA-inspired):
  Entity graph:  standard entities + RELATES_TO
  Temporal graph: TemporalEvent nodes + PARTICIPATED_IN
  Causal graph:  CausalLink nodes + CAUSED_BY
  Jiang et al., "MAGMA" (2026): orthogonal graph layers.

References:
  - He et al., "HippoRAG" (2024): KG + Personalized PageRank for retrieval.
  - Edge et al., "GraphRAG" (2024): entity graph + community summaries.
  - Rasmussen et al., "Zep" (2025): bi-temporal KG architecture.
  - Jiang et al., "MAGMA" (2026): multi-graph agentic memory.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from prax.services.memory.models import Entity
from prax.settings import settings

logger = logging.getLogger(__name__)

_driver = None


def _get_driver():
    """Lazy-init the Neo4j driver."""
    global _driver
    if _driver is not None:
        return _driver
    from neo4j import GraphDatabase

    uri = getattr(settings, "neo4j_uri", "bolt://localhost:7687")
    user = getattr(settings, "neo4j_user", "neo4j")
    password = getattr(settings, "neo4j_password", "prax-memory")
    _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver


def _ensure_indexes(session) -> None:
    """Create indexes if they don't exist (idempotent)."""
    indexes = [
        "CREATE INDEX entity_user_name IF NOT EXISTS FOR (e:Entity) ON (e.user_id, e.name)",
        "CREATE INDEX entity_user_type IF NOT EXISTS FOR (e:Entity) ON (e.user_id, e.type)",
        "CREATE INDEX entity_id IF NOT EXISTS FOR (e:Entity) ON (e.id)",
        "CREATE INDEX temporal_user IF NOT EXISTS FOR (t:TemporalEvent) ON (t.user_id)",
        "CREATE INDEX causal_user IF NOT EXISTS FOR (c:CausalLink) ON (c.user_id)",
    ]
    for idx in indexes:
        try:
            session.run(idx)
        except Exception:
            pass  # Already exists or syntax varies by Neo4j version


_indexes_created = False


def _session():
    """Get a Neo4j session, ensuring indexes exist."""
    global _indexes_created
    driver = _get_driver()
    session = driver.session()
    if not _indexes_created:
        _ensure_indexes(session)
        _indexes_created = True
    return session


def merge_entity(
    user_id: str,
    name: str,
    entity_type: str,
    display_name: str | None = None,
    importance: float = 0.5,
    properties: dict | None = None,
) -> str:
    """Upsert an entity node.  Returns the entity id.

    If the entity already exists (matched by user_id + lowercased name),
    increments mention_count and updates last_seen.  Otherwise creates new.
    """
    canonical = name.strip().lower()
    display = display_name or name
    now = datetime.now(UTC).isoformat()
    eid = str(uuid.uuid4())
    props = properties or {}

    try:
        with _session() as session:
            result = session.run(
                """
                MERGE (e:Entity {user_id: $uid, name: $name})
                ON CREATE SET
                    e.id = $eid,
                    e.display_name = $display,
                    e.type = $type,
                    e.importance = $importance,
                    e.mention_count = 1,
                    e.first_seen = $now,
                    e.last_seen = $now,
                    e.properties = $props
                ON MATCH SET
                    e.mention_count = e.mention_count + 1,
                    e.last_seen = $now,
                    e.importance = CASE WHEN $importance > e.importance
                                       THEN $importance ELSE e.importance END
                RETURN e.id AS id
                """,
                uid=user_id,
                name=canonical,
                eid=eid,
                display=display,
                type=entity_type,
                importance=importance,
                now=now,
                props=str(props),
            )
            record = result.single()
            return record["id"] if record else eid
    except Exception:
        logger.exception("Failed to merge entity '%s' for user %s", name, user_id)
        return eid


def add_relation(
    user_id: str,
    source_name: str,
    relation_type: str,
    target_name: str,
    weight: float = 1.0,
    evidence: str = "",
    valid_from: str | None = None,
) -> bool:
    """Create or strengthen a relation between two entities.

    Both entities must already exist (matched by user_id + name).
    If the relation exists, its weight is incremented and last_seen is updated.

    Bi-temporal: valid_from records when the fact became true (defaults to now).
    valid_until is null (currently valid) on creation.  Use supersede_relation()
    to mark an edge as no longer current.
    """
    src = source_name.strip().lower()
    tgt = target_name.strip().lower()
    now = datetime.now(UTC).isoformat()
    vf = valid_from or now

    try:
        with _session() as session:
            session.run(
                """
                MATCH (s:Entity {user_id: $uid, name: $src})
                MATCH (t:Entity {user_id: $uid, name: $tgt})
                MERGE (s)-[r:RELATES_TO {type: $rtype}]->(t)
                ON CREATE SET
                    r.weight = $weight,
                    r.first_seen = $now,
                    r.last_seen = $now,
                    r.evidence = $evidence,
                    r.valid_from = $vf,
                    r.valid_until = null
                ON MATCH SET
                    r.weight = r.weight + $weight,
                    r.last_seen = $now,
                    r.evidence = r.evidence + '; ' + $evidence
                """,
                uid=user_id,
                src=src,
                tgt=tgt,
                rtype=relation_type,
                weight=weight,
                now=now,
                evidence=evidence,
                vf=vf,
            )
            return True
    except Exception:
        logger.exception(
            "Failed to add relation %s -[%s]-> %s for user %s",
            source_name,
            relation_type,
            target_name,
            user_id,
        )
        return False


def supersede_relation(
    user_id: str,
    source_name: str,
    relation_type: str,
    target_name: str,
) -> bool:
    """Mark a relation as no longer current by setting valid_until = now.

    Used when consolidation detects a contradicting fact (e.g., user now prefers
    light mode → supersede the old "prefers dark mode" edge).

    Inspired by Rasmussen et al., "Zep" (2025): bi-temporal supersession.
    """
    src = source_name.strip().lower()
    tgt = target_name.strip().lower()
    now = datetime.now(UTC).isoformat()

    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (s:Entity {user_id: $uid, name: $src})
                      -[r:RELATES_TO {type: $rtype}]->
                      (t:Entity {user_id: $uid, name: $tgt})
                WHERE r.valid_until IS NULL
                SET r.valid_until = $now
                RETURN count(r) AS updated
                """,
                uid=user_id,
                src=src,
                tgt=tgt,
                rtype=relation_type,
                now=now,
            )
            record = result.single()
            return bool(record and record["updated"] > 0)
    except Exception:
        logger.exception("Failed to supersede relation %s -[%s]-> %s", source_name, relation_type, target_name)
        return False


def get_entity(user_id: str, name: str, include_superseded: bool = False) -> Entity | None:
    """Look up an entity by name, including its relations.

    By default only returns currently-valid relations (valid_until IS NULL).
    Set include_superseded=True to also see historical/superseded edges.
    """
    canonical = name.strip().lower()
    try:
        with _session() as session:
            result = session.run(
                f"""
                MATCH (e:Entity {{user_id: $uid, name: $name}})
                OPTIONAL MATCH (e)-[r:RELATES_TO]-(other:Entity)
                WHERE r IS NULL OR r.valid_until IS NULL {'OR true' if include_superseded else ''}
                RETURN e, collect({{
                    type: r.type,
                    weight: r.weight,
                    direction: CASE WHEN startNode(r) = e THEN 'outgoing' ELSE 'incoming' END,
                    other_name: other.display_name,
                    other_type: other.type,
                    valid_from: r.valid_from,
                    valid_until: r.valid_until
                }}) AS relations
                """,
                uid=user_id,
                name=canonical,
            )
            record = result.single()
            if not record:
                return None

            node = record["e"]
            rels = [r for r in record["relations"] if r.get("type") is not None]

            return Entity(
                id=node.get("id", ""),
                name=node.get("name", ""),
                display_name=node.get("display_name", ""),
                entity_type=node.get("type", ""),
                importance=node.get("importance", 0.5),
                mention_count=node.get("mention_count", 0),
                first_seen=node.get("first_seen", ""),
                last_seen=node.get("last_seen", ""),
                properties=node.get("properties", {}),
                relations=rels,
            )
    except Exception:
        logger.exception("Failed to get entity '%s' for user %s", name, user_id)
        return None


def get_neighbours(
    user_id: str,
    entity_name: str,
    max_hops: int = 2,
    limit: int = 30,
) -> list[dict]:
    """Traverse the graph around an entity up to max_hops.

    Returns a list of {entity, relation_type, distance} dicts representing
    the neighbourhood.  Useful for multi-hop reasoning and associative recall.

    Inspired by HippoRAG's Personalized PageRank neighbourhood retrieval.
    """
    canonical = entity_name.strip().lower()
    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (start:Entity {user_id: $uid, name: $name})
                CALL apoc.path.expandConfig(start, {
                    maxLevel: $hops,
                    uniqueness: 'NODE_GLOBAL',
                    limit: $limit
                })
                YIELD path
                WITH last(nodes(path)) AS node,
                     last(relationships(path)) AS rel,
                     length(path) AS distance
                RETURN node.display_name AS name,
                       node.type AS type,
                       node.importance AS importance,
                       rel.type AS relation_type,
                       rel.weight AS weight,
                       distance
                ORDER BY distance, rel.weight DESC
                """,
                uid=user_id,
                name=canonical,
                hops=max_hops,
                limit=limit,
            )
            return [dict(r) for r in result]
    except Exception:
        # APOC may not be installed — fall back to simple 1-hop
        logger.debug("APOC traversal failed — falling back to simple query")
        return _simple_neighbours(user_id, canonical, limit)


def _simple_neighbours(user_id: str, name: str, limit: int = 30) -> list[dict]:
    """Simple 1-hop neighbourhood query (no APOC dependency)."""
    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (e:Entity {user_id: $uid, name: $name})-[r:RELATES_TO]-(other:Entity)
                RETURN other.display_name AS name,
                       other.type AS type,
                       other.importance AS importance,
                       r.type AS relation_type,
                       r.weight AS weight,
                       1 AS distance
                ORDER BY r.weight DESC
                LIMIT $limit
                """,
                uid=user_id,
                name=name,
                limit=limit,
            )
            return [dict(r) for r in result]
    except Exception:
        logger.exception("Simple neighbour query failed for '%s'", name)
        return []


def search_entities(user_id: str, query: str, limit: int = 10) -> list[dict]:
    """Search entities by name substring (case-insensitive)."""
    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (e:Entity {user_id: $uid})
                WHERE toLower(e.display_name) CONTAINS toLower($search_term)
                   OR toLower(e.name) CONTAINS $search_term_lower
                RETURN e.id AS id,
                       e.display_name AS name,
                       e.type AS type,
                       e.importance AS importance,
                       e.mention_count AS mentions
                ORDER BY e.mention_count DESC
                LIMIT $limit
                """,
                uid=user_id,
                search_term=query,
                search_term_lower=query.strip().lower(),
                limit=limit,
            )
            return [dict(r) for r in result]
    except Exception:
        logger.exception("Entity search failed for '%s'", query)
        return []


def decay_graph(user_id: str, halflife_days: float = 14.0, prune_threshold: float = 0.05) -> int:
    """Apply exponential decay to entity importance and relation weights.

    Returns the number of entities/relations pruned.
    """
    import math

    lambda_ = math.log(2) / halflife_days
    now = datetime.now(UTC)
    pruned = 0

    try:
        with _session() as session:
            # Decay entity importance
            result = session.run(
                """
                MATCH (e:Entity {user_id: $uid})
                WITH e,
                     duration.between(datetime(e.last_seen), datetime($now)).days AS days_elapsed
                SET e.importance = e.importance * exp(-$lambda * days_elapsed)
                RETURN count(e) AS updated
                """,
                uid=user_id,
                now=now.isoformat(),
                lambda_=lambda_,
            )

            # Decay relation weights
            session.run(
                """
                MATCH (:Entity {user_id: $uid})-[r:RELATES_TO]-(:Entity)
                WITH r,
                     duration.between(datetime(r.last_seen), datetime($now)).days AS days_elapsed
                SET r.weight = r.weight * exp(-$lambda * days_elapsed)
                """,
                uid=user_id,
                now=now.isoformat(),
                lambda_=lambda_,
            )

            # Prune low-importance entities without relations
            result = session.run(
                """
                MATCH (e:Entity {user_id: $uid})
                WHERE e.importance < $threshold
                  AND NOT (e)-[:RELATES_TO]-()
                DELETE e
                RETURN count(e) AS pruned
                """,
                uid=user_id,
                threshold=prune_threshold,
            )
            record = result.single()
            pruned = record["pruned"] if record else 0

            # Prune weak relations
            result = session.run(
                """
                MATCH (:Entity {user_id: $uid})-[r:RELATES_TO]-(:Entity)
                WHERE r.weight < $threshold
                DELETE r
                RETURN count(r) AS pruned
                """,
                uid=user_id,
                threshold=prune_threshold / 2,
            )
            record = result.single()
            pruned += record["pruned"] if record else 0

    except Exception:
        logger.exception("Graph decay failed for user %s", user_id)

    return pruned


def merge_temporal_event(
    user_id: str,
    description: str,
    occurred_at: str | None = None,
    importance: float = 0.5,
    participant_names: list[str] | None = None,
) -> str:
    """Create a TemporalEvent node and link participating entities.

    TemporalEvent nodes form the temporal graph layer — they represent
    discrete events with a timestamp.  Entities are linked via
    PARTICIPATED_IN edges.

    Inspired by MAGMA (Jiang et al., 2026): orthogonal temporal graph.
    """
    now = datetime.now(UTC).isoformat()
    eid = str(uuid.uuid4())
    ts = occurred_at or now

    try:
        with _session() as session:
            session.run(
                """
                CREATE (t:TemporalEvent {
                    id: $eid,
                    user_id: $uid,
                    description: $desc,
                    occurred_at: $ts,
                    importance: $importance,
                    created_at: $now
                })
                """,
                eid=eid,
                uid=user_id,
                desc=description[:500],
                ts=ts,
                importance=importance,
                now=now,
            )
            # Link participating entities
            for name in (participant_names or []):
                canonical = name.strip().lower()
                session.run(
                    """
                    MATCH (e:Entity {user_id: $uid, name: $name})
                    MATCH (t:TemporalEvent {id: $eid})
                    MERGE (e)-[:PARTICIPATED_IN]->(t)
                    """,
                    uid=user_id,
                    name=canonical,
                    eid=eid,
                )
            return eid
    except Exception:
        logger.exception("Failed to merge temporal event for user %s", user_id)
        return eid


def add_causal_link(
    user_id: str,
    cause_description: str,
    effect_description: str,
    cause_entity_names: list[str] | None = None,
    effect_entity_names: list[str] | None = None,
    importance: float = 0.5,
) -> str:
    """Create a CausalLink node connecting cause and effect entities.

    CausalLink nodes form the causal graph layer — they represent
    why-relationships between entities/events.

    Inspired by MAGMA (Jiang et al., 2026): orthogonal causal graph.
    """
    now = datetime.now(UTC).isoformat()
    cid = str(uuid.uuid4())

    try:
        with _session() as session:
            session.run(
                """
                CREATE (c:CausalLink {
                    id: $cid,
                    user_id: $uid,
                    cause: $cause,
                    effect: $effect,
                    importance: $importance,
                    created_at: $now
                })
                """,
                cid=cid,
                uid=user_id,
                cause=cause_description[:500],
                effect=effect_description[:500],
                importance=importance,
                now=now,
            )
            # Link cause entities
            for name in (cause_entity_names or []):
                canonical = name.strip().lower()
                session.run(
                    """
                    MATCH (e:Entity {user_id: $uid, name: $name})
                    MATCH (c:CausalLink {id: $cid})
                    MERGE (e)-[:CAUSED_BY {direction: 'cause'}]->(c)
                    """,
                    uid=user_id,
                    name=canonical,
                    cid=cid,
                )
            # Link effect entities
            for name in (effect_entity_names or []):
                canonical = name.strip().lower()
                session.run(
                    """
                    MATCH (e:Entity {user_id: $uid, name: $name})
                    MATCH (c:CausalLink {id: $cid})
                    MERGE (c)-[:CAUSED_BY {direction: 'effect'}]->(e)
                    """,
                    uid=user_id,
                    name=canonical,
                    cid=cid,
                )
            return cid
    except Exception:
        logger.exception("Failed to add causal link for user %s", user_id)
        return cid


def get_stats(user_id: str) -> dict:
    """Return summary stats for a user's graph."""
    try:
        with _session() as session:
            result = session.run(
                """
                OPTIONAL MATCH (e:Entity {user_id: $uid})
                WITH count(DISTINCT e) AS ent_count
                OPTIONAL MATCH (:Entity {user_id: $uid})-[r:RELATES_TO]-()
                WITH ent_count, count(DISTINCT r) AS rel_count
                OPTIONAL MATCH (t:TemporalEvent {user_id: $uid})
                WITH ent_count, rel_count, count(DISTINCT t) AS evt_count
                OPTIONAL MATCH (c:CausalLink {user_id: $uid})
                RETURN ent_count AS entities,
                       rel_count AS relations,
                       evt_count AS temporal_events,
                       count(DISTINCT c) AS causal_links
                """,
                uid=user_id,
            )
            record = result.single()
            return {
                "entities": record["entities"] if record else 0,
                "relations": record["relations"] if record else 0,
                "temporal_events": record["temporal_events"] if record else 0,
                "causal_links": record["causal_links"] if record else 0,
            }
    except Exception:
        return {"entities": 0, "relations": 0, "temporal_events": 0, "causal_links": 0}


def close() -> None:
    """Close the Neo4j driver (call on shutdown)."""
    global _driver
    if _driver:
        _driver.close()
        _driver = None
