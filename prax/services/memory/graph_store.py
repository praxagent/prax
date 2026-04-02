"""Neo4j-backed property graph for structured entity/relation memory.

Stores entities (people, topics, projects, concepts, tools) and typed
relations between them.  All queries are scoped by user_id.  Supports
multi-hop traversal and Personalized PageRank-style neighbourhood queries.

Gracefully degrades: if Neo4j is unreachable, operations return empty
results and log warnings.

Graph model:
  (:Entity {id, user_id, name, display_name, type, importance,
            first_seen, last_seen, mention_count, properties})
  -[:RELATES_TO {type, weight, first_seen, last_seen, evidence}]->

References:
  - He et al., "HippoRAG" (2024): KG + Personalized PageRank for retrieval.
  - Edge et al., "GraphRAG" (2024): entity graph + community summaries.
  - Angles & Gutierrez (2008): property graph formalism.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

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
    now = datetime.now(timezone.utc).isoformat()
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
) -> bool:
    """Create or strengthen a relation between two entities.

    Both entities must already exist (matched by user_id + name).
    If the relation exists, its weight is incremented and last_seen is updated.
    """
    src = source_name.strip().lower()
    tgt = target_name.strip().lower()
    now = datetime.now(timezone.utc).isoformat()

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
                    r.evidence = $evidence
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


def get_entity(user_id: str, name: str) -> Entity | None:
    """Look up an entity by name, including its relations."""
    canonical = name.strip().lower()
    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (e:Entity {user_id: $uid, name: $name})
                OPTIONAL MATCH (e)-[r:RELATES_TO]-(other:Entity)
                RETURN e, collect({
                    type: r.type,
                    weight: r.weight,
                    direction: CASE WHEN startNode(r) = e THEN 'outgoing' ELSE 'incoming' END,
                    other_name: other.display_name,
                    other_type: other.type
                }) AS relations
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
                WHERE toLower(e.display_name) CONTAINS toLower($query)
                   OR toLower(e.name) CONTAINS $query_lower
                RETURN e.id AS id,
                       e.display_name AS name,
                       e.type AS type,
                       e.importance AS importance,
                       e.mention_count AS mentions
                ORDER BY e.mention_count DESC
                LIMIT $limit
                """,
                uid=user_id,
                query=query,
                query_lower=query.strip().lower(),
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
    now = datetime.now(timezone.utc)
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


def get_stats(user_id: str) -> dict:
    """Return summary stats for a user's graph."""
    try:
        with _session() as session:
            result = session.run(
                """
                MATCH (e:Entity {user_id: $uid})
                OPTIONAL MATCH (e)-[r:RELATES_TO]-()
                RETURN count(DISTINCT e) AS entities,
                       count(DISTINCT r) AS relations
                """,
                uid=user_id,
            )
            record = result.single()
            return {
                "entities": record["entities"] if record else 0,
                "relations": record["relations"] if record else 0,
            }
    except Exception:
        return {"entities": 0, "relations": 0}


def close() -> None:
    """Close the Neo4j driver (call on shutdown)."""
    global _driver
    if _driver:
        _driver.close()
        _driver = None
